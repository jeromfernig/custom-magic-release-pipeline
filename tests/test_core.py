from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from mtg_pipeline.canonicalize import canonicalize
from mtg_pipeline.config import AppConfig, SourceConfig
from mtg_pipeline.exporters.cockatrice import export_cockatrice
from mtg_pipeline.exporters.cubecobra import CUBECOBRA_HEADER, export_cubecobra
from mtg_pipeline.exporters.draftmancer import export_draftmancer
from mtg_pipeline.exporters.print_pdf import print_copy_count
from mtg_pipeline.images import public_image_url
from mtg_pipeline.mana import infer_colors_from_mana_cost, normalize_mana_cost
from mtg_pipeline.manifest import write_manifest
from mtg_pipeline.pipeline import PipelineRunResult, StepResult
from mtg_pipeline.steps import build_pipeline
from mtg_pipeline.utils.filenames import safe_card_stem
from mtg_pipeline.validation import validate_source

from tests.fixtures import sample_source


def test_canonical_model_parsing() -> None:
    set_def, cards = canonicalize(sample_source(), AppConfig())
    by_name = {card.name: card for card in cards}
    assert set_def.set_id == "pokemon-mtg-set"
    assert set_def.set_code == "PKM"
    assert set_def.release_date is None
    assert by_name["Tideflame Adept"].colors == ["U", "R"]
    assert by_name["Meteor Valley"].render_profile == "fullart_colorless"
    assert by_name["Sky Monarch"].supertypes == ["Legendary"]
    assert by_name["Sky Monarch"].card_types == ["Creature"]
    assert by_name["Sky Monarch"].subtypes == ["Bird", "Noble"]
    assert by_name["Sky Monarch"].type_line == "Legendary Creature - Bird Noble"
    assert by_name["Verdant Seedling"].mana_cost == "{G}"
    assert by_name["Verdant Seedling"].collector_number == "004"
    assert by_name["Verdant Seedling"].rendered_image_filename == "004-verdant-seedling.png"


def test_source_scopes_accept_yaml_string_forms() -> None:
    assert SourceConfig(scopes="https://www.googleapis.com/auth/spreadsheets").scopes == ["https://www.googleapis.com/auth/spreadsheets"]
    assert SourceConfig(scopes="scope-a, scope-b").scopes == ["scope-a", "scope-b"]


def test_blank_release_date_is_treated_as_absent() -> None:
    source = sample_source()
    source.set_metadata["release_date"] = ""
    set_def, _ = canonicalize(source, AppConfig())
    assert set_def.release_date is None


def test_collector_numbers_are_derived_by_group_then_name() -> None:
    _, cards = canonicalize(sample_source(), AppConfig())
    assert [(card.collector_number, card.name) for card in cards] == [
        ("001", "Ancient Null"),
        ("002", "Quiet Study"),
        ("003", "Static Burst"),
        ("004", "Verdant Seedling"),
        ("005", "Sky Monarch"),
        ("006", "Tideflame Adept"),
        ("007", "Training Charm"),
        ("008", "Meteor Valley"),
    ]


def test_basic_lands_sort_after_nonbasic_lands() -> None:
    source = sample_source()
    source.card_rows.append({
        "id": "pkm-009",
        "name": "Island",
        "mana_cost": "",
        "mana_value": "0",
        "supertypes": "Basic",
        "card_types": "Land",
        "subtypes": "Island",
        "oracle_text": "({T}: Add {U}.)",
        "flavor_text": "",
        "comment_text": "",
        "power": "",
        "toughness": "",
        "loyalty": "",
        "rarity": "land",
        "artist": "Tester",
        "artwork_url": "https://example.com/art/island.jpg",
        "render_profile": "standard",
    })
    _, cards = canonicalize(source, AppConfig())
    assert [(card.collector_number, card.name) for card in cards[-2:]] == [
        ("008", "Meteor Valley"),
        ("009", "Island"),
    ]


def test_validation_success_and_counts() -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(sample_source(), config)
    assert result.ok
    assert result.rarity_counts["common"] == 3
    assert result.rarity_counts["mythic"] == 1
    assert result.rarity_counts["land"] == 1


def test_mana_cost_normalization_and_color_inference() -> None:
    assert normalize_mana_cost("2GG") == "{2}{G}{G}"
    assert normalize_mana_cost("{w}{u}") == "{W}{U}"
    assert infer_colors_from_mana_cost("3WU") == ["W", "U"]
    assert infer_colors_from_mana_cost("7") == []


def test_layout_defaults_to_normal_when_column_missing_or_blank() -> None:
    source = sample_source()
    source.card_rows[0].pop("layout")
    source.card_rows[1]["layout"] = ""
    _, cards = canonicalize(source, AppConfig())
    assert all(card.layout == "normal" for card in cards)


def test_validation_rejects_bad_rarity_and_version() -> None:
    source = sample_source()
    source.set_metadata["version"] = "draft"
    source.card_rows[0]["rarity"] = "legendary"
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(source, config)
    assert not result.ok
    assert any("legendary" in issue.message for issue in result.errors)
    assert any("semantic versioning" in issue.message for issue in result.errors)


def test_validation_requires_stable_set_id() -> None:
    source = sample_source()
    source.set_metadata.pop("set_id")
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(source, config)
    assert not result.ok
    assert any("set_id is required" in issue.message for issue in result.errors)


def test_validation_requires_card_type_not_type_line() -> None:
    source = sample_source()
    source.card_rows[0].pop("type_line", None)
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(source, config)
    assert result.ok
    source.card_rows[0]["card_types"] = ""
    result = validate_source(source, config)
    assert not result.ok
    assert any("card_types is required" in issue.message for issue in result.errors)


def test_validation_does_not_require_input_collector_number() -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(sample_source(), config)
    assert result.ok


def test_validation_warns_when_input_collector_number_is_present() -> None:
    source = sample_source()
    source.card_rows[0]["collector_number"] = "999"
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(source, config)
    assert result.ok
    assert any("collector_number column is ignored" in issue.message for issue in result.warnings)


def test_validation_warns_when_art_source_missing() -> None:
    source = sample_source()
    source.card_rows[0]["artwork_url"] = ""
    source.card_rows[0]["art_prompt"] = ""
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    result = validate_source(source, config)
    assert result.ok
    assert any("placeholder artwork will be used" in issue.message for issue in result.warnings)


def test_filename_generation() -> None:
    assert safe_card_stem("001A", "Mewtwo's Test Card!") == "001a-mewtwo-s-test-card"


def test_dependency_resolution() -> None:
    runner = build_pipeline()
    states = runner.availability({"source", "validation", "canonicalization", "print"})
    assert states["print"][0] is False
    assert "Render cards" in states["print"][1]


def test_draftmancer_export(tmp_path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    config.images.public_image_base_url = "https://cdn.example/cards"
    set_def, cards = canonicalize(sample_source(), config)
    path = export_draftmancer(set_def, cards, config, tmp_path / "draft.txt")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("[CustomCards]")
    assert "[Common]" in text
    assert "Verdant Seedling (PKM) 004" in text


def test_cubecobra_export(tmp_path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    set_def, cards = canonicalize(sample_source(), config)
    path = export_cubecobra(set_def, cards, config, tmp_path / "cube.csv")
    first_line = path.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert first_line == CUBECOBRA_HEADER


def test_cockatrice_export_escapes_xml(tmp_path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    source = sample_source()
    source.card_rows[0]["oracle_text"] = "Choose A & B, then say \"go\"."
    set_def, cards = canonicalize(source, config)
    path = export_cockatrice(set_def, cards, config, tmp_path / "cockatrice.xml")
    tree = ET.parse(path)
    assert tree.getroot().attrib["version"] == "4"
    texts_by_name = {card.find("name").text: card.find("text").text for card in tree.findall("./cards/card")}
    assert texts_by_name["Verdant Seedling"] == "Choose A & B, then say \"go\"."


def test_print_copy_count_from_config() -> None:
    config = AppConfig()
    config.print.copies_by_rarity["common"] = 4
    set_def, cards = canonicalize(sample_source(), config)
    common = next(card for card in cards if card.rarity == "common")
    assert print_copy_count(common, config.print) == 4


def test_manifest_generation(tmp_path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    set_def, cards = canonicalize(sample_source(), config)
    run = PipelineRunResult(steps={"source": StepResult(id="source", status="success")})
    path = write_manifest(set_def, cards, {"source"}, run, tmp_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["set_id"] == "pokemon-mtg-set"
    assert manifest["set_name"] == "Pokemon MTG Set"
    assert manifest["counts_by_rarity"]["rare"] == 1
    assert manifest["counts_by_rarity"]["land"] == 1


def test_public_image_url_uses_stable_set_id() -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    config.images.public_image_base_url = "https://cdn.example/custom"
    set_def, cards = canonicalize(sample_source(), config)
    by_name = {card.name: card for card in cards}
    assert public_image_url(config.images.public_image_base_url, set_def, by_name["Verdant Seedling"]) == "https://cdn.example/custom/pokemon-mtg-set/0.3.0/cards/004-verdant-seedling.png"
