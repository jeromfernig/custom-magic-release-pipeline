from __future__ import annotations

import ipaddress
import logging
import os
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

import boto3
from PIL import Image
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from mtg_pipeline.config import R2Config
from mtg_pipeline.utils.cache import atomic_write, file_digest
from mtg_pipeline.linked_cards import image_cards, physical_cards, linked_face


def r2_settings(config):
    return (config.bucket or os.environ.get("R2_BUCKET", ""),
            (config.public_url or os.environ.get("R2_PUBLIC_URL", "")).rstrip("/"),
            all(os.environ.get(k, "").strip() for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")))


def validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("R2 public URL must be an HTTPS public domain without credentials or query parameters.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", "r2.cloudflarestorage.com")):
        raise ValueError("R2 public URL must not be local or the private S3 endpoint.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("R2 public URL must not use a private IP address.")


class CloudflareR2Client:
    def __init__(self, config: R2Config):
        self.bucket, self.public_base, configured = r2_settings(config)
        if not configured or not self.bucket or not self.public_base:
            raise RuntimeError("Configure R2 credentials, bucket and public URL before hosting artwork.")
        validate_public_url(self.public_base)
        self.client = boto3.client("s3", endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID'].strip()}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto", config=Config(connect_timeout=15, read_timeout=60,
                retries={"mode": "standard", "total_max_attempts": 3}, signature_version="s3v4",
                request_checksum_calculation="when_required", response_checksum_validation="when_required",
                s3={"addressing_style": "path", "payload_signing_enabled": False}))

    def public_url(self, key: str) -> str:
        if not key or key.startswith("/") or ".." in PurePosixPath(key).parts or "\\" in key:
            raise ValueError("Invalid R2 object key.")
        return f"{self.public_base}/{quote(key, safe='/')}"

    def upload_file(self, path: Path, key: str, content_type: str) -> bool:
        self.public_url(key)
        digest = file_digest(path)
        operation = "checking whether the object already exists"
        try:
            try:
                existing = self.client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
                    raise
                existing = None
            if existing and existing.get("Metadata", {}).get("sha256") == digest and existing.get("ContentLength") == path.stat().st_size:
                return False
            operation = "uploading the object"
            for attempt in range(3):
                try:
                    with path.open("rb") as body:
                        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType=content_type,
                            Metadata={"sha256": digest}, CacheControl="no-cache")
                    break
                except BotoCoreError as exc:
                    if attempt == 2:
                        raise
                    delay = 2 ** attempt
                    logging.getLogger("mtg_pipeline").warning(
                        "R2 transport reset while uploading %s; reopening file and retrying in %s second(s): %s",
                        path.name, delay, exc,
                    )
                    time.sleep(delay)
            return True
        except ClientError as exc:
            error = exc.response.get("Error", {})
            metadata = exc.response.get("ResponseMetadata", {})
            code = error.get("Code", "unknown service error")
            message = error.get("Message", "no service message")
            request_id = metadata.get("RequestId")
            detail = f"R2 upload failed while {operation} in bucket {self.bucket}: {code}: {message}."
            if request_id:
                detail += f" Cloudflare request ID: {request_id}."
            raise RuntimeError(detail) from None
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 upload failed while {operation}: {type(exc).__name__}: {exc}") from None
        except OSError as exc:
            raise RuntimeError(f"R2 upload failed while reading {path.name}: {type(exc).__name__}: {exc}") from None


def image_type(path):
    with Image.open(path) as image:
        image.verify()
        return {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}[image.format]


def publish_source_artwork(cards, config, project_root, progress, statistics):
    cards = image_cards(cards)
    root = project_root.resolve()
    cache = (root / config.cache.artwork).resolve()
    if not cache.is_relative_to(root) or cache == root or cache.is_relative_to((root / config.paths.release_root).resolve()):
        raise ValueError("Artwork cache must be inside the project and outside releases.")
    stats = statistics.setdefault("artwork", {"local_cache_hits": 0, "r2_reused": 0, "r2_uploaded": 0, "files": 0})
    publisher = CloudflareR2Client(config.r2)
    published = set()
    outputs = []
    for index, card in enumerate(cards, 1):
        path = (root / (card.artwork_path or "")).resolve()
        if not card.artwork_path or not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(f"Prepared artwork is unavailable for {card.name}.")
        extension, content_type = image_type(path)
        digest = file_digest(path)
        local = cache / f"{digest}.{extension}"
        hit = local.is_file() and file_digest(local) == digest
        if not hit:
            atomic_write(local, path.read_bytes())
        stats["local_cache_hits"] += int(hit)
        key = f"{config.r2.artwork_prefix.strip('/')}/{digest}.{extension}"
        uploaded = False
        if key not in published:
            uploaded = publisher.upload_file(local, key, content_type)
            published.add(key)
        stats["r2_uploaded" if uploaded else "r2_reused"] += 1
        stats["files"] += 1
        card.artwork_path = local.relative_to(root).as_posix()
        card.artwork_sha256, card.artwork_object_key = digest, key
        card.artwork_public_url = publisher.public_url(key)
        outputs.append(local)
        progress(f"Artwork {index}/{len(cards)}: {card.name} - {'local cache hit' if hit else 'cached source'}; R2 {'uploaded' if uploaded else 'object reused'}")
    progress(f"Artwork summary: {stats}")
    return outputs


def publish_rendered_cards(cards, set_def, config, release_dir, progress):
    if any(not card.render_cache_key for card in cards):
        raise RuntimeError("Render all cards before publishing finished-card URLs; set the selected renderer's render_limit to null.")
    publisher = CloudflareR2Client(config.r2)
    all_cards = cards
    cards = image_cards(cards)
    for index, card in enumerate(cards, 1):
        path = release_dir / "cards" / card.expected_rendered_filename
        key = f"{config.r2.rendered_cards_prefix.strip('/')}/{set_def.set_id}/{set_def.version}/cards/{path.name}"
        publisher.upload_file(path, key, "image/jpeg")
        card.rendered_public_url = publisher.public_url(key)
        progress(f"Hosted rendered card {index}/{len(cards)}: {card.name}")
    for card in physical_cards(all_cards):
        if card.link_type == "prepare" and (face := linked_face(card, all_cards)):
            face.rendered_public_url = card.rendered_public_url
    return []
