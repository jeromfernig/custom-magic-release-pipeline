from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from mtg_pipeline.artwork.r2 import CloudflareR2Client, image_type
from mtg_pipeline.config import load_config


SAFE_HEADERS = {
    "content-length",
    "content-type",
    "transfer-encoding",
    "expect",
    "x-amz-content-sha256",
    "x-amz-sdk-checksum-algorithm",
}


def _safe_headers(headers) -> dict[str, str]:
    return {name: str(value) for name, value in headers.items() if name.lower() in SAFE_HEADERS}


def _print_request(request, **_kwargs) -> None:
    print(f"Request: {request.method} {request.url}")
    print(f"Safe request headers: {_safe_headers(request.headers)}")


def _print_response(http_response, **_kwargs) -> None:
    headers = http_response.headers
    print(f"Response: HTTP {http_response.status_code}; CF-RAY: {headers.get('cf-ray', 'not supplied')}")


def _describe_error(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        request_id = exc.response.get("ResponseMetadata", {}).get("RequestId", "not supplied")
        return f"R2 service error: {error.get('Code', 'unknown')}: {error.get('Message', 'no message')}; request ID: {request_id}"
    if isinstance(exc, BotoCoreError):
        return f"BotoCore transport error: {type(exc).__name__}: {exc}"
    return f"Unexpected error: {type(exc).__name__}: {exc}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Safely diagnose R2 signed reads and uploads.")
    parser.add_argument("--config", default="config.example.yaml", help="Pipeline YAML configuration file.")
    parser.add_argument("--upload-probe", action="store_true", help="Upload a small diagnostic text object under debug/.")
    parser.add_argument("--file", type=Path, help="Upload a local file under debug/ to reproduce an upload failure.")
    args = parser.parse_args()
    if args.upload_probe == bool(args.file):
        parser.error("Specify exactly one of --upload-probe or --file.")

    config = load_config(Path(args.config))
    publisher = CloudflareR2Client(config.r2)
    client = publisher.client
    client.meta.events.register("before-send.s3", _print_request)
    client.meta.events.register("after-call.s3", _print_response)
    probe_key = f"debug/r2-read-probe-{uuid.uuid4().hex}.txt"

    print(f"Bucket: {publisher.bucket}")
    print(f"Endpoint: https://{os.environ['R2_ACCOUNT_ID'].strip()}.r2.cloudflarestorage.com")
    try:
        client.head_object(Bucket=publisher.bucket, Key=probe_key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
            print("Signed metadata read reached R2 and returned the expected missing-object response.")
        else:
            raise SystemExit(_describe_error(exc)) from None
    except Exception as exc:
        raise SystemExit(_describe_error(exc)) from None

    if args.file:
        path = args.file.resolve()
        if not path.is_file():
            raise SystemExit(f"Diagnostic file does not exist: {path}")
        extension, content_type = image_type(path)
        key = f"debug/r2-file-probe-{uuid.uuid4().hex}.{extension}"
        body = path.open("rb")
        label = f"{path.name} ({path.stat().st_size} bytes)"
    else:
        key = f"debug/r2-upload-probe-{uuid.uuid4().hex}.txt"
        content_type = "text/plain"
        body = b"r2 upload diagnostic\n"
        label = "small text probe"
    try:
        print(f"Uploading {label} to {key}")
        if hasattr(body, "__enter__"):
            with body:
                client.put_object(Bucket=publisher.bucket, Key=key, Body=body, ContentType=content_type,
                                  Metadata={"purpose": "pipeline-r2-debug"}, CacheControl="no-cache")
        else:
            client.put_object(Bucket=publisher.bucket, Key=key, Body=body, ContentType=content_type,
                              Metadata={"purpose": "pipeline-r2-debug"}, CacheControl="no-cache")
    except Exception as exc:
        raise SystemExit(_describe_error(exc)) from None
    print(f"Upload succeeded. Diagnostic object retained at: {key}")


if __name__ == "__main__":
    main()
