from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

from mtg_pipeline.artwork.r2 import validate_public_url
from mtg_pipeline.renderers.mtg_design_postprocess import postprocess_release_jpeg
from mtg_pipeline.utils.cache import atomic_write, file_digest, write_json

AUTH_ERROR = "MTG.Design authentication failed. The Laravel session token appears to be missing or expired."
RARITIES = {"common": "C", "uncommon": "U", "rare": "R", "mythic": "M", "land": "C"}
SET_ICON_FIELDS = {
    "common": "set_icon_common",
    "uncommon": "set_icon_uncommon",
    "rare": "set_icon_rare",
    "mythic": "set_icon_mythic",
    "land": "set_icon_common",
}


class _MtgDesignHtmlPage(HTMLParser):
    """Extract only user-visible diagnostic text; never echo request parameters."""

    def __init__(self) -> None:
        super().__init__()
        self._tags: list[str] = []
        self.title: list[str] = []
        self.messages: list[str] = []
        self.is_login_page = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        self._tags.append(tag)
        if tag == "form" and "login" in (values.get("action") or "").casefold():
            self.is_login_page = True
        if tag == "input" and values.get("name") in {"email", "password"}:
            self.is_login_page = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.pop(len(self._tags) - 1 - self._tags[::-1].index(tag))

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if "title" in self._tags:
            self.title.append(text)
        if any(tag in self._tags for tag in {"h1", "h2", "h3", "p"}):
            self.messages.append(text)


def _response_body(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def _html_response_error(content: bytes) -> str:
    page = _MtgDesignHtmlPage()
    page.feed(content.decode("utf-8", errors="replace"))
    title = " ".join(page.title).strip()
    messages = " ".join(dict.fromkeys(page.messages)).strip()
    login_words = f"{title} {messages}".casefold()
    if page.is_login_page or title.casefold() in {"login", "sign in"} or "sign in to mtg.design" in login_words:
        return f"{AUTH_ERROR}\n\nMTG.Design response body:\n{_response_body(content)}"
    summary = title or messages
    if summary:
        summary = summary[:400]
        return f"MTG.Design returned an HTML error page instead of a JPEG: {summary}\n\nMTG.Design response body:\n{_response_body(content)}"
    return f"MTG.Design returned an HTML error page instead of a JPEG.\n\nMTG.Design response body:\n{_response_body(content)}"


def _authentication_response_error(response) -> str:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    return f"{AUTH_ERROR}\nHTTP status: {response.status_code}\nContent-Type: {content_type}\n\nMTG.Design response body:\n{_response_body(response.content)}"


def normalize_params(params: dict) -> dict[str, str]:
    def value(item):
        if item is None:
            return ""
        if isinstance(item, bool):
            return "true" if item else "false"
        if isinstance(item, float):
            item = format(item, ".15g")
        return unicodedata.normalize("NFC", str(item).replace("\r\n", "\n").replace("\r", "\n"))
    return {str(key): value(item) for key, item in params.items()}


def request_hash(params: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def build_mtg_design_params(card, set_definition, render_context) -> dict[str, str]:
    if card.layout != "normal" or card.loyalty is not None or "Planeswalker" in card.card_types:
        raise ValueError(f"MTG.Design mapping for {card.name} requires a verified layout/loyalty configuration; only normal non-planeswalker cards are supported.")
    if not card.artwork_object_key or not card.artwork_public_url:
        raise ValueError(f"{card.name} needs an R2 artwork upload before rendering.")
    validate_public_url(card.artwork_public_url)
    template = card.colors[0] if len(card.colors) == 1 else "Gld" if card.colors else "C"
    accent = next((pair for pair in ("WU", "UB", "BR", "RG", "GW", "WB", "RW", "UR", "GU", "BG")
                   if set(pair) == set(card.colors)), template)
    if len(card.colors) == 2 and "/" in (card.mana_cost or ""):
        template = accent
    params = {
        "card-title": card.name, "mana-cost": card.mana_cost or "",
        "super-type": " ".join(card.supertypes), "type": " ".join(card.card_types), "sub-type": " ".join(card.subtypes),
        "card-number": card.collector_number, "card-total": len(render_context.cards), "card-set": set_definition.set_code,
        "rarity": RARITIES[card.rarity], "rules-text": card.oracle_text or "",
        "flavor-text": card.flavor_text or "",
        "artist": card.artist or "", "power": card.power or "", "toughness": card.toughness or "",
        "card-template": template, "card-accent": accent, "card-layout": "regular", "land-overlay": "C",
        "set-symbol": _set_symbol(set_definition, card.rarity),
    }
    params.update(render_context.config.mtg_design.defaults)
    params.update(render_context.config.render_profiles[card.render_profile].renderer_options)
    text_sizing = render_context.config.mtg_design.text_sizing
    if text_sizing.enabled:
        params["text-size"] = _rules_text_size(card.oracle_text or "", text_sizing)
    params["artwork"] = card.artwork_public_url
    params["edit"] = False
    return normalize_params(params)


def _set_symbol(set_definition, rarity: str) -> str:
    return getattr(set_definition, SET_ICON_FIELDS[rarity], None) or "0"


def _rules_text_size(oracle_text: str, config) -> int:
    """Estimate MTG.Design's text fit using hard breaks and 60-character wraps."""
    visible_lines = [re.sub(r"<[^>]+>", "", line).strip() for line in oracle_text.replace("\r", "").split("\n")]
    estimated_lines = sum(
        max(1, math.ceil(len(line) / config.estimated_characters_per_line))
        for line in visible_lines
        if line
    )
    if estimated_lines <= 1:
        return config.maximum_size
    decrease = math.ceil((estimated_lines - 1) * config.size_decrease_per_line)
    return max(config.minimum_size, config.maximum_size - decrease)


def validate_jpeg(content: bytes) -> bytes:
    if len(content) < 128 or not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("MTG.Design response is not JPEG data.")
    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            if image.format != "JPEG" or image.height <= image.width:
                raise ValueError("Expected a portrait JPEG.")
    except (OSError, ValueError):
        raise ValueError("Invalid or corrupt JPEG data.") from None
    return content


class MtgDesignRendererClient:
    def __init__(self, config, laravel_session: str = ""):
        if any(ord(char) < 33 or ord(char) > 126 or char == ";" for char in laravel_session):
            raise ValueError(AUTH_ERROR)
        if config.base_url.rstrip("/") != "https://mtg.design":
            raise ValueError("MTG.Design cookies may only be sent to https://mtg.design.")
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "custom-mtg-set-builder/0.1"
        self.session.cookies.set("laravel_session", laravel_session, domain="mtg.design", path="/", secure=True)
        self.has_session = bool(laravel_session)
        self.last_request_at = None

    def close(self):
        self.session.cookies.clear()
        self.session.close()

    def render(self, params: dict[str, str]) -> bytes:
        if not self.has_session:
            raise RuntimeError(AUTH_ERROR)
        for attempt in range(self.config.max_attempts):
            if self.last_request_at is not None:
                wait = max(0, self.config.request_delay_seconds - (time.monotonic() - self.last_request_at))
                if wait:
                    time.sleep(wait)
            self.last_request_at = time.monotonic()
            response = None
            try:
                response = self.session.get("https://mtg.design/render", params=params,
                    timeout=self.config.timeout_seconds, allow_redirects=False)
            except (requests.ConnectionError, requests.Timeout):
                pass
            except requests.RequestException:
                raise RuntimeError("MTG.Design request failed.") from None
            if response is not None:
                content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
                if response.status_code in {301, 302, 303, 307, 308, 401, 403} or "/login" in response.url:
                    raise RuntimeError(_authentication_response_error(response))
                if response.status_code == 200:
                    content = response.content
                    if content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                        raise RuntimeError(_html_response_error(response.content))
                    try:
                        # MTG.Design can send valid JFIF data with a text/html content type.
                        # The JPEG signature and decoder are the authoritative validation.
                        return validate_jpeg(content)
                    except ValueError:
                        if content_type == "text/html":
                            raise RuntimeError(_html_response_error(content)) from None
                        raise RuntimeError("MTG.Design returned a non-JPEG response; no image was saved.") from None
                if response.status_code != 429 and not 500 <= response.status_code <= 599:
                    raise RuntimeError(f"MTG.Design returned HTTP {response.status_code}.")
            if attempt + 1 < self.config.max_attempts:
                delay = 5 * 2 ** attempt
                if response is not None:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                time.sleep(delay)
        raise RuntimeError("MTG.Design request failed after the configured retry attempts.")


def render_design_cards(context):
    root = context.project_root.resolve()
    cache = (root / context.config.cache.mtg_design).resolve()
    if not cache.is_relative_to(root) or cache == root or cache.is_relative_to((root / context.config.paths.release_root).resolve()):
        raise ValueError("Render cache must be inside the project and outside releases.")
    config = context.config.mtg_design
    cards = context.cards if not config.render_limit or config.render_limit < 0 else context.cards[:config.render_limit]
    stats = context.statistics.setdefault("renderer", {"name": "mtg.design", "request_delay_seconds": config.request_delay_seconds,
        "cards_processed": 0, "cache_hits": 0, "remote_renders": 0, "failures": 0})
    client = MtgDesignRendererClient(config, context.mtg_design_session)
    outputs = []
    try:
        for index, card in enumerate(cards, 1):
            art = (root / (card.artwork_path or "")).resolve()
            if not card.artwork_path or not art.is_relative_to(root) or file_digest(art) != card.artwork_sha256:
                raise ValueError(f"Artwork changed after R2 upload for {card.name}; rerun preparation.")
            params = build_mtg_design_params(card, context.set_definition, context)
            text_size = int(params["text-size"])
            threshold = config.text_sizing.warning_below_size
            if config.text_sizing.enabled and text_size < threshold:
                context.warnings.append(
                    f"MTG.Design text size {text_size} for {card.collector_number} - {card.name} is below "
                    f"the readability threshold of {threshold}."
                )
            key = request_hash(params)
            cached_path = cache / "renders" / f"{key}.jpg"
            try:
                content = validate_jpeg(cached_path.read_bytes())
            except (OSError, ValueError):
                content = None
            context.report_progress(f"Rendering {index}/{len(cards)}: {card.name} - {'cache hit' if content else 'requesting MTG.Design'}")
            if content is None:
                content = client.render(params)
                atomic_write(cached_path, content)
                write_json(cache / "metadata" / f"{key}.json", {"request_hash": key, "request_params": params,
                    "renderer": "mtg.design", "byte_size": len(content), "content_type": "image/jpeg"})
                stats["remote_renders"] += 1
            else:
                stats["cache_hits"] += 1
            output = root / context.release_dir / "cards" / f"{card.file_stem}.jpg"
            atomic_write(output, content)
            postprocess_release_jpeg(output, context.config.mtg_design.postprocess)
            card.rendered_image_filename, card.render_cache_key = output.name, key
            card.rendered_image_path = cached_path.relative_to(root).as_posix()
            outputs.append(output)
            stats["cards_processed"] += 1
    except Exception:
        stats["failures"] += 1
        raise
    finally:
        client.close()
        context.report_progress(f"Rendering summary: {stats}")
    return outputs
