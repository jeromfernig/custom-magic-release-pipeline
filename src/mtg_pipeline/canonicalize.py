from __future__ import annotations

import json
from pathlib import Path

from mtg_pipeline.config import AppConfig
from mtg_pipeline.collector import assign_collector_numbers
from mtg_pipeline.mana import infer_colors_from_mana_cost, normalize_mana_cost
from mtg_pipeline.models import CardDefinition, SetDefinition, SourceData
from mtg_pipeline.linked_cards import physical_cards, resolve_rows


def canonicalize(data: SourceData, config: AppConfig) -> tuple[SetDefinition, list[CardDefinition]]:
    set_def = SetDefinition.model_validate(data.set_metadata)
    cards: list[CardDefinition] = []
    m = config.columns
    for row in resolve_rows(data.card_rows, m):
        if str(row.get(m.include, "")).strip().lower() in {"false", "no", "0", "skip", "disabled"}:
            continue
        card = CardDefinition(
            id=_value(row, m.id),
            name=_value(row, m.name),
            record_type=_optional(row, m.record_type) or "card",
            link_id=_optional(row, m.link_id),
            link_type=_optional(row, m.link_type),
            set_code=set_def.set_code,
            rarity=_value(row, m.rarity).lower(),
            mana_cost=normalize_mana_cost(_optional(row, m.mana_cost)),
            mana_value=_float_or_none(row.get(m.mana_value)),
            colors=_optional(row, m.color_indicator) or infer_colors_from_mana_cost(_optional(row, m.mana_cost)),
            color_indicator=_optional(row, m.color_indicator),
            supertypes=_optional(row, m.super_types),
            card_types=_optional(row, m.card_types),
            subtypes=_optional(row, m.subtypes),
            type_line=_optional(row, m.type_line) or "",
            oracle_text=_optional(row, m.oracle_text),
            flavor_text=_optional(row, m.flavor_text),
            comment_text=_optional(row, m.comment_text),
            art_prompt=_optional(row, m.art_prompt),
            archetype=row.get(m.archetype),
            power=_optional(row, m.power),
            toughness=_optional(row, m.toughness),
            loyalty=_optional(row, m.loyalty),
            battle_defense=_optional(row, m.battle_defense),
            artist=_optional(row, m.artist),
            artwork_url=_optional(row, m.artwork_url),
            layout=_optional(row, m.layout) or "normal",
            render_profile=_optional(row, m.render_profile) or "standard",
            rendered_image_filename=None,
            source_row_number=_int_or_none(row.get("__row_number")),
        )
        cards.append(card)
    primaries = assign_collector_numbers(physical_cards(cards))
    by_id = {card.id: card for card in cards}
    for card in primaries:
        if card.link_id:
            by_id[card.link_id].collector_number = card.collector_number
    cards = primaries + [card for card in cards if card.record_type == "linked_face"]
    for card in cards:
        card.rendered_image_filename = f"{card.file_stem}.jpg"
    for card in primaries:
        if card.link_type == "transform":
            face = by_id[card.link_id]
            face.rendered_image_filename = f"{face.file_stem}-back.jpg"
    return set_def, cards


def write_canonical_json(set_def: SetDefinition, cards: list[CardDefinition], release_dir: Path) -> list[Path]:
    data_dir = release_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    set_path = data_dir / "set.json"
    cards_path = data_dir / "cards.json"
    set_path.write_text(set_def.model_dump_json(indent=2) + "\n", encoding="utf-8")
    cards_json = [card.model_dump(mode="json") for card in cards]
    cards_path.write_text(json.dumps(cards_json, indent=2) + "\n", encoding="utf-8")
    return [set_path, cards_path]


def _value(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    return "" if value is None else str(value).strip()


def _optional(row: dict[str, object], key: str) -> str | None:
    value = _value(row, key)
    return value or None


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value).strip())


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value).strip())
