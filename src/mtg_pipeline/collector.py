from __future__ import annotations

from mtg_pipeline.models import CardDefinition

COLLECTOR_GROUPS = [
    "Colorless nonartifact",
    "White",
    "Blue",
    "Black",
    "Red",
    "Green",
    "Multicolor",
    "Artifact",
    "Nonbasic land",
    "Basic land",
]

COLOR_GROUP_INDEX = {
    "W": 1,
    "U": 2,
    "B": 3,
    "R": 4,
    "G": 5,
}


def assign_collector_numbers(cards: list[CardDefinition]) -> list[CardDefinition]:
    sorted_cards = sorted(cards, key=lambda card: (_collector_group_index(card), card.name.casefold(), card.id))
    width = max(3, len(str(len(sorted_cards))))
    for index, card in enumerate(sorted_cards, start=1):
        card.collector_number = str(index).zfill(width)
        card.rendered_image_filename = None
        card.rendered_image_filename = card.expected_rendered_filename
    return sorted_cards


def collector_group(card: CardDefinition) -> str:
    return COLLECTOR_GROUPS[_collector_group_index(card)]


def _collector_group_index(card: CardDefinition) -> int:
    card_types = {part.casefold() for part in card.card_types}
    supertypes = {part.casefold() for part in card.supertypes}
    if "land" in card_types and "basic" in supertypes:
        return 9
    if "land" in card_types:
        return 8
    if "artifact" in card_types:
        return 7
    if len(card.colors) > 1:
        return 6
    if len(card.colors) == 1:
        return COLOR_GROUP_INDEX.get(card.colors[0], 0)
    return 0
