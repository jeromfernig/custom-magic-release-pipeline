from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from mtg_pipeline.config import AppConfig
from mtg_pipeline.images import public_image_url
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.linked_cards import physical_cards, linked_face


RARITY_SHEETS = {
    "common": "Common",
    "uncommon": "Uncommon",
    "rare": "Rare",
    "mythic": "Mythic",
    "land": "Land",
}


def sheet_counts(cards: list[CardDefinition]) -> dict[str, int]:
    rarities = Counter(card.rarity for card in physical_cards(cards))
    return {**{sheet: rarities[rarity] for rarity, sheet in RARITY_SHEETS.items()},
            "RareMythic": rarities["rare"] + rarities["mythic"]}


def pack_layout_errors(settings: dict, available: dict[str, int] | None = None) -> list[str]:
    errors = []
    layouts = settings.get("layouts")
    if not isinstance(layouts, dict) or not layouts:
        return ["Define at least one pack layout."]
    for option in ("withReplacement", "refillWhenEmpty"):
        if option in settings and not isinstance(settings[option], bool):
            errors.append(f"{option} must be true or false.")
    sheets = set(RARITY_SHEETS.values()) | {"RareMythic"}
    for name, layout in layouts.items():
        if not isinstance(layout, dict):
            errors.append(f"Pack layout {name} must be an object.")
            continue
        weight = layout.get("weight")
        if type(weight) is not int or weight <= 0:
            errors.append(f"Pack layout {name} needs a positive integer weight.")
        slots = layout.get("slots")
        if isinstance(slots, dict):
            slots = [{"name": sheet, "count": count} for sheet, count in slots.items()]
        if not isinstance(slots, list) or not slots:
            errors.append(f"Pack layout {name} must contain at least one slot.")
            continue
        for slot in slots:
            if not isinstance(slot, dict) or not isinstance(slot.get("name"), str):
                errors.append(f"Pack layout {name} has an invalid slot.")
                continue
            count = slot.get("count")
            if type(count) is not int or count <= 0:
                errors.append(f"{name}: {slot['name']} needs a positive integer card count.")
            sources = slot.get("sheets", [{"name": slot["name"], "weight": 1}])
            if not isinstance(sources, list) or not sources:
                errors.append(f"{name}: {slot['name']} needs at least one source sheet.")
                continue
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("name"), str) or source["name"] not in sheets:
                    errors.append(f"{name}: {slot['name']} refers to an unknown sheet.")
                    continue
                if type(source.get("weight")) is not int or source["weight"] <= 0:
                    errors.append(f"{name}: sheet {source['name']} needs a positive integer weight.")
                if available is not None and available.get(source["name"], 0) == 0:
                    errors.append(f"{name}: {source['name']} is requested but contains no cards.")
    return errors


def export_draftmancer(set_def: SetDefinition, cards: list[CardDefinition], config: AppConfig, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    custom_cards = []
    all_cards = cards
    cards = physical_cards(cards)
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
        if face := linked_face(card, all_cards):
            if card.link_type == "transform":
                custom_cards[-1]["back"] = {
                    "name": face.name, "image": public_image_url(config.images.public_image_base_url, set_def, face),
                    "type": _main_type(face.type_line), "subtypes": face.subtypes, "mana_cost": face.mana_cost or "",
                    "oracle_text": face.oracle_text or "",
                    **{field: getattr(face, field) for field in ("power", "toughness", "loyalty") if getattr(face, field)},
                }
            else:
                custom_cards[-1]["oracle_text"] += f"\n\nPrepared spell — {face.name} {face.mana_cost or ''}\n{face.type_line}\n{face.oracle_text or ''}"

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
