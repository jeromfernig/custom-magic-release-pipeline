from __future__ import annotations

from mtg_pipeline.models import SourceData


def sample_source() -> SourceData:
    card_rows = [
        _row("pkm-001", "Verdant Seedling", "G", "1", "", "Creature", "Plant", "When Verdant Seedling enters, gain 1 life.", "1", "1", "", "common"),
        _row("pkm-002", "Static Burst", "1R", "2", "", "Instant", "", "Static Burst deals 2 damage to any target.", "", "", "", "common"),
        _row("pkm-003", "Tideflame Adept", "UR", "2", "", "Creature", "Wizard", "Whenever you cast an instant, scry 1.", "2", "2", "", "uncommon"),
        _row("pkm-004", "Training Charm", "2", "2", "", "Artifact", "", "{T}: Add one mana of any color.", "", "", "", "uncommon"),
        _row("pkm-005", "Meteor Valley", "", "0", "", "Land", "", "{T}: Add {C}.", "", "", "", "land", render_profile="fullart_colorless"),
        _row("pkm-006", "Sky Monarch", "3WU", "5", "Legendary", "Creature", "Bird Noble", "Flying, vigilance.", "4", "4", "", "mythic"),
        _row("pkm-007", "Quiet Study", "1U", "2", "", "Sorcery", "", "Draw two cards, then discard a card.", "", "", "", "common"),
        _row("pkm-008", "Ancient Null", "7", "7", "", "Creature", "Eldrazi", "Ward {2}.", "6", "6", "", "rare", render_profile="fullart_colorless"),
    ]
    for row_number, row in enumerate(card_rows, start=2):
        row["__row_number"] = str(row_number)
    return SourceData(
        set_metadata={
            "set_id": "pokemon-mtg-set",
            "set_name": "Pokemon MTG Set",
            "set_code": "PKM",
            "version": "0.3.0",
            "draft_mode": "false",
        },
        card_rows=card_rows,
    )


def _row(
    row_id: str,
    name: str,
    mana_cost: str,
    mana_value: str,
    supertypes: str,
    card_types: str,
    subtypes: str,
    oracle_text: str,
    power: str,
    toughness: str,
    loyalty: str,
    rarity: str,
    *,
    render_profile: str = "standard",
) -> dict[str, str]:
    return {
        "id": row_id,
        "name": name,
        "mana_cost": mana_cost,
        "mana_value": mana_value,
        "supertypes": supertypes,
        "card_types": card_types,
        "subtypes": subtypes,
        "oracle_text": oracle_text,
        "flavor_text": f"Flavor for {name}.",
        "comment_text": f"Draft note for {name}.",
        "art_prompt": "",
        "power": power,
        "toughness": toughness,
        "loyalty": loyalty,
        "rarity": rarity,
        "artist": "Tester",
        "artwork_url": f"https://example.com/art/{row_id}.jpg",
        "layout": "normal",
        "render_profile": render_profile,
    }
