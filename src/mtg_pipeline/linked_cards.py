"""Flat canonical face records and their physical-card relationships."""
from __future__ import annotations

from collections import Counter

from mtg_pipeline.models import CardDefinition


def physical_cards(cards):
    return [card for card in cards if card.record_type == "card"]


def linked_face(card, cards):
    return next((face for face in cards if face.id == card.link_id), None) if card.link_id else None


def image_cards(cards):
    """Faces with their own image: primary cards and transforming back faces."""
    backs = {card.link_id for card in physical_cards(cards) if card.link_type == "transform"}
    return [card for card in cards if card.record_type == "card" or card.id in backs]


def display_cards(cards):
    """Keep physical fronts and backs adjacent, without printing prepare insets twice."""
    from mtg_pipeline.exporters.print_pdf import _print_order

    result = []
    for card in sorted(physical_cards(cards), key=_print_order):
        result.append(card)
        if card.link_type == "transform" and (face := linked_face(card, cards)):
            result.append(face)
    return result


def resolve_rows(rows, mapping):
    """Resolve blank shared metadata; reject ambiguous/dangling/recursive links."""
    active = []
    for index, row in enumerate(rows, 2):
        if str(row.get(mapping.include, "")).strip().lower() in {"false", "no", "0", "skip", "disabled"}:
            continue
        copy = dict(row)
        copy.setdefault("__row_number", index)
        active.append(copy)
    text = lambda row, key: str(row.get(key) or "").strip()
    ids = [text(row, mapping.id) for row in active]
    duplicates = [key for key, count in Counter(ids).items() if key and count > 1]
    if duplicates:
        raise ValueError("Duplicate card IDs: " + ", ".join(duplicates))
    by_id = dict(zip(ids, active))
    owners = {}
    for row in active:
        card_id = text(row, mapping.id)
        kind = text(row, mapping.record_type) or "card"
        if kind not in {"card", "linked_face"}:
            raise ValueError(f"{card_id}: record_type must be card or linked_face.")
        target, link_type = text(row, mapping.link_id), text(row, mapping.link_type)
        if bool(target) != bool(link_type):
            raise ValueError(f"{card_id}: link_id and link_type must be set together.")
        if not target:
            continue
        if kind != "card":
            raise ValueError(f"{card_id}: a linked face cannot link to another record.")
        if link_type not in {"prepare", "transform"}:
            raise ValueError(f"{card_id}: link_type must be prepare or transform.")
        if target == card_id or target not in by_id:
            raise ValueError(f"{card_id}: link_id {target!r} must reference another included record.")
        child = by_id[target]
        if text(child, mapping.record_type) != "linked_face":
            raise ValueError(f"{card_id}: linked record {target} must have record_type linked_face.")
        if target in owners:
            raise ValueError(f"{target}: linked face is referenced by multiple cards.")
        owners[target] = row
    for row in active:
        card_id = text(row, mapping.id)
        if text(row, mapping.record_type) != "linked_face":
            continue
        if card_id not in owners:
            raise ValueError(f"{card_id}: orphan linked face; remove/disable it or link it from a card.")
        parent = owners[card_id]
        for key in (mapping.rarity, mapping.render_profile, mapping.archetype):
            if not text(row, key):
                row[key] = parent.get(key)
        if not text(row, mapping.oracle_text):
            raise ValueError(f"{card_id}: oracle_text is required for a linked face.")
    return active


def validate_links(cards: list[CardDefinition]):
    """Apply the same graph rules to canonical JSON loaded outside the pipeline."""
    from mtg_pipeline.config import ColumnMapping
    resolve_rows([card.model_dump() for card in cards], ColumnMapping())
