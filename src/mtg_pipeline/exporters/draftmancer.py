from __future__ import annotations

import json
from pathlib import Path

from mtg_pipeline.config import AppConfig
from mtg_pipeline.images import public_image_url
from mtg_pipeline.models import CardDefinition, SetDefinition


RARITY_SHEETS = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "mythic": "Mythic",
    "land": "Land",
}


def export_draftmancer(set_def: SetDefinition, cards: list[CardDefinition], config: AppConfig, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    custom_cards = []
    for card in cards:
        custom_cards.append({
            "name": card.name,
            "mana_cost": card.mana_cost or "",
            "type": _main_type(card.type_line),
            "subtypes": _subtypes(card.type_line),
            "oracle_text": card.oracle_text or "",
            "colors": card.colors,
            "set": set_def.set_code,
            "collector_number": card.collector_number,
            "rarity": card.rarity,
            "image": public_image_url(config.images.public_image_base_url, set_def, card),
            **({"power": card.power} if card.power else {}),
            **({"toughness": card.toughness} if card.toughness else {}),
            **({"loyalty": card.loyalty} if card.loyalty else {}),
        })

    lines = [
        "[CustomCards]",
        json.dumps(custom_cards, indent=2, ensure_ascii=False),
        "[Settings]",
        json.dumps(config.draft.booster_layout, indent=2, ensure_ascii=False),
    ]
    for rarity, sheet_name in RARITY_SHEETS.items():
        lines.append(f"[{sheet_name}]")
        for card in cards:
            if card.rarity == rarity:
                lines.append(f"{card.name} ({set_def.set_code}) {card.collector_number}")
    lines.append("[RareMythic]")
    for card in cards:
        if card.rarity in {"rare", "mythic"}:
            lines.append(f"{card.name} ({set_def.set_code}) {card.collector_number}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _main_type(type_line: str) -> str:
    return type_line.split(" - ", 1)[0]


def _subtypes(type_line: str) -> list[str]:
    if " - " not in type_line:
        return []
    return [part.strip() for part in type_line.split(" - ", 1)[1].split() if part.strip()]
