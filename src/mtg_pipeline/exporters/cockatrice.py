from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mtg_pipeline.config import AppConfig
from mtg_pipeline.images import public_image_url
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.linked_cards import physical_cards, linked_face


def export_cockatrice(set_def: SetDefinition, cards: list[CardDefinition], config: AppConfig, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("cockatrice_carddatabase", {"version": "4"})
    sets_node = ET.SubElement(root, "sets")
    set_node = ET.SubElement(sets_node, "set")
    ET.SubElement(set_node, "name").text = set_def.set_code
    ET.SubElement(set_node, "longname").text = set_def.set_name
    ET.SubElement(set_node, "settype").text = "Custom"
    if set_def.release_date:
        ET.SubElement(set_node, "releasedate").text = set_def.release_date.isoformat()

    cards_node = ET.SubElement(root, "cards")
    for card in physical_cards(cards):
        node = _add_card(cards_node, set_def, card, config)
        face = linked_face(card, cards)
        if face and card.link_type == "transform":
            node.find("prop/layout").text = "transform"
            ET.SubElement(node, "related", {"attach": "transform"}).text = face.name
            back = _add_card(cards_node, set_def, face, config)
            back.find("prop/layout").text = "transform"
            back.find("prop/side").text = "back"
        elif face:
            node.find("text").text = (card.oracle_text or "") + f"\n\nPrepared spell — {face.name} {face.mana_cost or ''}\n{face.type_line}\n{face.oracle_text or ''}"

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def _add_card(parent: ET.Element, set_def: SetDefinition, card: CardDefinition, config: AppConfig) -> ET.Element:
    card_node = ET.SubElement(parent, "card")
    ET.SubElement(card_node, "name").text = card.name
    ET.SubElement(card_node, "text").text = card.oracle_text or ""
    prop = ET.SubElement(card_node, "prop")
    ET.SubElement(prop, "layout").text = card.layout
    ET.SubElement(prop, "side").text = "front"
    ET.SubElement(prop, "type").text = card.type_line
    ET.SubElement(prop, "maintype").text = _main_type(card.type_line)
    ET.SubElement(prop, "manacost").text = _cockatrice_mana_cost(card.mana_cost)
    ET.SubElement(prop, "cmc").text = "" if card.mana_value is None else str(card.mana_value)
    ET.SubElement(prop, "colors").text = "".join(card.colors)
    if card.power or card.toughness:
        ET.SubElement(prop, "pt").text = f"{card.power or ''}/{card.toughness or ''}"
    if card.loyalty:
        ET.SubElement(prop, "loyalty").text = card.loyalty
    attrs = {
        "rarity": card.rarity,
        "num": card.collector_number,
        "picurl": public_image_url(config.images.public_image_base_url, set_def, card),
    }
    ET.SubElement(card_node, "set", attrs).text = set_def.set_code
    return card_node


def _main_type(type_line: str) -> str:
    return type_line.split(" - ", 1)[0].split()[-1]


def _cockatrice_mana_cost(mana_cost: str | None) -> str:
    return "" if not mana_cost else mana_cost.replace("{", "").replace("}", "")
