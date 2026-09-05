from __future__ import annotations

from urllib.parse import urljoin

from mtg_pipeline.models import CardDefinition, SetDefinition


def public_image_url(base_url: str, set_def: SetDefinition, card: CardDefinition) -> str:
    if not base_url:
        return ""
    base = base_url.rstrip("/") + "/"
    return urljoin(base, f"{set_def.set_id}/{set_def.version}/cards/{card.expected_rendered_filename}")
