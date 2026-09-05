from __future__ import annotations

import json
from pathlib import Path

from mtg_pipeline.config import AppConfig
from mtg_pipeline.collector import assign_collector_numbers
from mtg_pipeline.mana import infer_colors_from_mana_cost, normalize_mana_cost
from mtg_pipeline.models import CardDefinition, SetDefinition, SourceData


def canonicalize(data: SourceData, config: AppConfig) -> tuple[SetDefinition, list[CardDefinition]]:
    set_def = SetDefinition.model_validate(data.set_metadata)
    cards: list[CardDefinition] = []
    m = config.columns
    for row in data.card_rows:
        if str(row.get(m.include, "")).strip().lower() in {"false", "no", "0", "skip", "disabled"}:
            continue
        card = CardDefinition(
            id=_value(row, m.id),
            name=_value(row, m.name),
            set_code=set_def.set_code,
            rarity=_value(row, m.rarity).lower(),
            mana_cost=normalize_mana_cost(_optional(row, m.mana_cost)),
            mana_value=_float_or_none(row.get(m.mana_value)),
            colors=infer_colors_from_mana_cost(_optional(row, m.mana_cost)),
            supertypes=_optional(row, m.super_types),
            card_types=_optional(row, m.card_types),
            subtypes=_optional(row, m.subtypes),
            type_line=_optional(row, m.type_line) or "",
            oracle_text=_optional(row, m.oracle_text),
            flavor_text=_optional(row, m.flavor_text),
            comment_text=_optional(row, m.comment_text),
            art_prompt=_optional(row, m.art_prompt),
            power=_optional(row, m.power),
            toughness=_optional(row, m.toughness),
            loyalty=_optional(row, m.loyalty),
            artist=_optional(row, m.artist),
            artwork_url=_optional(row, m.artwork_url),
            layout=_optional(row, m.layout) or "normal",
            render_profile=_optional(row, m.render_profile) or "standard",
            rendered_image_filename=None,
            source_row_number=_int_or_none(row.get("__row_number")),
        )
        cards.append(card)
    return set_def, assign_collector_numbers(cards)


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
    return str(row.get(key, "")).strip()


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
