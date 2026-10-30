from __future__ import annotations

import csv
from pathlib import Path

from mtg_pipeline.config import AppConfig
from mtg_pipeline.images import public_image_url
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.linked_cards import physical_cards, linked_face

CUBECOBRA_HEADER = [
    "name",
    "CMC",
    "Type",
    "Color",
    "Set",
    "Collector Number",
    "Rarity",
    "Color Category",
    "status",
    "Finish",
    "maybeboard",
    "image URL",
    "image Back URL",
    "tags",
    "Notes",
    "MTGO ID",
]


def export_cubecobra(set_def: SetDefinition, cards: list[CardDefinition], config: AppConfig, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CUBECOBRA_HEADER)
        for card in physical_cards(cards):
            face = linked_face(card, cards) if card.link_type == "transform" else None
            writer.writerow([
                card.name,
                "" if card.mana_value is None else card.mana_value,
                card.type_line,
                "".join(card.colors),
                set_def.set_code,
                card.collector_number,
                card.rarity,
                _color_category(card),
                "Owned",
                "Non-foil",
                "false",
                public_image_url(config.images.public_image_base_url, set_def, card),
                public_image_url(config.images.public_image_base_url, set_def, face) if face else "",
                "",
                "Generated custom-card row. Validate import manually in CubeCobra.",
                "",
            ])
    return output_path


def _color_category(card: CardDefinition) -> str:
    if "Land" in card.type_line:
        return "Land"
    if len(card.colors) > 1:
        return "Multicolor"
    if len(card.colors) == 1:
        return {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}.get(card.colors[0], "Colorless")
    return "Colorless"
