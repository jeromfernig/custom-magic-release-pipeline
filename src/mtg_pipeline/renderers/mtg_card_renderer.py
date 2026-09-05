from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import requests

from mtg_pipeline.config import AppConfig
from mtg_pipeline.models import CardDefinition, SetDefinition


class MtgCardRendererClient:
    def __init__(self, base_url: str, output_format: str = "PNG", scale: int = 3, timeout_seconds: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.output_format = output_format.upper()
        self.scale = scale
        self.timeout_seconds = timeout_seconds

    def render_card(self, card: CardDefinition, output_path: Path, profile_options: dict[str, Any] | None = None, artwork_path: Path | None = None, use_comment_as_flavor: bool = False) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = card_to_renderer_payload(card, profile_options or {}, use_comment_as_flavor=use_comment_as_flavor)
        params = {"format": self.output_format, "scale": str(self.scale)}
        if artwork_path and artwork_path.exists():
            with artwork_path.open("rb") as image:
                files = {
                    "card": (None, json.dumps(payload), "application/json"),
                    "image": (artwork_path.name, image, _content_type(artwork_path)),
                }
                response = requests.post(f"{self.base_url}/api/render/upload", params=params, files=files, timeout=self.timeout_seconds)
        else:
            response = requests.post(f"{self.base_url}/api/render", params=params, json=payload, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            body = response.text[:500]
            raise RuntimeError(f"Rendering failed for {card.collector_number} - {card.name}: renderer returned HTTP {response.status_code}. {body}")
        output_path.write_bytes(response.content)
        card.rendered_image_filename = output_path.name
        return output_path


def card_to_renderer_payload(card: CardDefinition, profile_options: dict[str, Any] | None = None, use_comment_as_flavor: bool = False) -> dict[str, Any]:
    flavor_text = card.comment_text if use_comment_as_flavor else card.flavor_text
    payload: dict[str, Any] = {
        "name": card.name,
        "mana_cost": card.mana_cost or "",
        "cmc": card.mana_value,
        "type_line": card.type_line,
        "oracle_text": card.oracle_text or "",
        "flavor_text": flavor_text or "",
        "colors": card.colors,
        "set": card.set_code.lower(),
        "collector_number": card.collector_number,
        "rarity": card.rarity,
        "artist": card.artist or "",
        "lang": "en",
        "layout": card.layout,
    }
    if card.power is not None:
        payload["power"] = card.power
    if card.toughness is not None:
        payload["toughness"] = card.toughness
    if card.loyalty is not None:
        payload["loyalty"] = card.loyalty
    payload.update(profile_options or {})
    return payload


def render_selected_cards(set_def: SetDefinition, cards: list[CardDefinition], config: AppConfig, release_dir: Path, project_root: Path | None = None, progress: Callable[[str], None] | None = None) -> list[Path]:
    project_root = (project_root or Path.cwd()).resolve()
    client = MtgCardRendererClient(config.renderer.base_url, config.renderer.format, config.renderer.scale)
    limit = config.renderer.render_limit
    selected = cards if limit is None or limit <= 0 else cards[:limit]
    output_paths: list[Path] = []
    for index, card in enumerate(selected, start=1):
        if progress:
            progress(f"Rendering {index}/{len(selected)}: {card.name}")
        profile = config.render_profiles[card.render_profile]
        if not card.artwork_path:
            raise RuntimeError(f"Prepared artwork is missing for {card.name}. Run Prepare artwork first.")
        artwork_path = (project_root / card.artwork_path).resolve()
        if not artwork_path.is_relative_to(project_root) or not artwork_path.is_file():
            raise RuntimeError(f"Prepared artwork is unavailable for {card.name}: {card.artwork_path}")
        output_path = release_dir / "cards" / f"{card.file_stem}.{config.renderer.format.lower()}"
        output_paths.append(client.render_card(card, output_path, profile.renderer_options, artwork_path, use_comment_as_flavor=set_def.draft_mode))
    return output_paths


def _content_type(path: Path) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }.get(path.suffix.lower(), "application/octet-stream")
