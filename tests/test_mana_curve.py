import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from mtg_pipeline.config import AppConfig
from mtg_pipeline.exporters.mana_curve import analyze_mana_curve, card_asfan, export_mana_curve, mana_value
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.steps import build_pipeline


def card(card_id, rarity="common", colors=None, types=None, value=2, **kwargs):
    return CardDefinition(id=card_id, name=card_id, set_code="TST", rarity=rarity, colors=colors or [],
                          card_types=types or ["Creature"], mana_value=value, **kwargs)


def layout(slots):
    return {"layouts": {"Default": {"weight": 1, "slots": slots}}}


def test_rarity_weighting_uses_full_sheet_denominators_and_six_segments():
    cards = [card("CW01", colors=["W"]), card("CW02", colors=["W"], types=["Instant"]),
             card("UW01", rarity="uncommon", colors=["W"]), card("UW02", rarity="uncommon", colors=["W"], types=["Artifact"]),
             card("RW01", rarity="rare", colors=["W"]), card("MW01", rarity="mythic", colors=["W"], types=["Enchantment"])]
    result = analyze_mana_curve(cards, layout({"Common": 2, "Uncommon": 1, "RareMythic": 1}))
    segments = result["curves"]["W"]["2.0"]
    assert segments == {"common_creatures": 1, "common_noncreatures": 1, "uncommon_creatures": .5,
                        "uncommon_noncreatures": .5, "rare_creatures": .5, "rare_noncreatures": .5}
    assert sum(result["card_asfan"].values()) == pytest.approx(4)


def test_weighted_layouts_and_weighted_rare_mythic_sheet_selection():
    cards = [card("CW01"), card("RW01", "rare"), card("MW01", "mythic")]
    settings = {"layouts": {
        "Rare": {"weight": 7, "slots": {"Common": 1, "Rare": 1}},
        "Mythic": {"weight": 1, "slots": {"Common": 1, "Mythic": 1}},
    }}
    weights, draws = card_asfan(cards, settings)
    assert weights == {"CW01": 1, "RW01": .875, "MW01": .125}
    weighted_slot = layout([{"name": "RareOrMythic", "count": 1, "sheets": [{"name": "Rare", "weight": 7}, {"name": "Mythic", "weight": 1}]}])
    assert card_asfan(cards, weighted_slot)[0] == {"CW01": 0, "RW01": .875, "MW01": .125}


@pytest.mark.parametrize("sequence, expected", [
    (["Rare", "Rare", "Mythic"], 2 / 3),
    ([["Rare", "Mythic"]], .875),
    ([{"Rare": 1, "Mythic": 1}], .5),
    ([[{"name": "Rare", "weight": 3}, {"name": "Mythic", "weight": 1}]], .75),
])
def test_predetermined_layout_averages(sequence, expected):
    cards = [card("R01", "rare"), card("M01", "mythic")]
    settings = {"layouts": {"Rare": {"weight": 7, "slots": {"Rare": 1}}, "Mythic": {"weight": 1, "slots": {"Mythic": 1}}}, "predeterminedLayouts": sequence}
    weights, _ = card_asfan(cards, settings)
    assert weights["R01"] == pytest.approx(expected)
    assert weights["M01"] == pytest.approx(1 - expected)


def test_multicolor_full_membership_and_lands_excluded_only_from_curve():
    cards = [card("CM01", colors=["W", "U"]), card("CC01", types=["Land"]), card("CC02")]
    analysis = analyze_mana_curve(cards, layout({"Common": 3}))
    assert analysis["curves"]["W"]["2.0"]["common_creatures"] == 1
    assert analysis["curves"]["U"]["2.0"]["common_creatures"] == 1
    assert analysis["curves"]["C"]["2.0"]["common_creatures"] == 1
    assert analysis["excluded_lands"] == ["CC01"]
    assert analysis["card_asfan"]["CC01"] == 1


@pytest.mark.parametrize("cost, expected", [("{2}{W}{W}", 4), ("{X}{G}", 1), ("{2/W}{U/P}", 3), (None, 0)])
def test_mana_value_fallback(cost, expected):
    assert mana_value(card("CW01", value=None, mana_cost=cost)) == expected


def test_explicit_mana_value_is_authoritative_and_invalid_values_rejected():
    assert mana_value(card("CW01", value=7, mana_cost="{G}")) == 7
    for value in (-1, math.inf, math.nan):
        with pytest.raises(ValueError, match="Invalid mana value"):
            mana_value(card("CW01", value=value))


def test_empty_requested_rarity_fails_instead_of_faking_normalization():
    with pytest.raises(ValueError, match="RareMythic.*no cards"):
        analyze_mana_curve([card("CW01")], layout({"Common": 1, "RareMythic": 1}))


def test_pdf_and_json_export_have_six_color_pages_and_auditable_weights(tmp_path):
    cards = [card("CW01", colors=["W"]), card("CU01", colors=["U"], types=["Instant"], value=3)]
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="0.1.0")
    output = tmp_path / "curves.pdf"
    paths = export_mana_curve(set_def, cards, layout({"Common": 2}), output)
    assert paths[:2] == [output, output.with_suffix(".json")]
    assert len(paths) == 8 and all(path.is_file() for path in paths)
    assert b"/Count 6" in output.read_bytes()
    data = json.loads(paths[1].read_text())
    assert data["expected_pack_size"] == 2
    assert data["curves"]["U"]["3.0"]["common_noncreatures"] == 1


def test_pipeline_analysis_needs_only_canonicalization(tmp_path):
    config = AppConfig()
    runner = build_pipeline(config)
    selected = {"source", "validation", "canonicalization", "mana_curve"}
    assert runner.availability(selected)["mana_curve"][0]
    context = PipelineContext(config=config, release_dir=tmp_path, set_definition=SetDefinition(set_id="test", set_name="Test", set_code="TST", version="0.1.0"))
    with patch("mtg_pipeline.steps.export_mana_curve", return_value=[]) as export:
        runner.by_id["mana_curve"].execute(context)
    assert export.call_args.args[2] is config.draft.booster_layout
    assert export.call_args.args[3] == tmp_path / "analysis/tst-0.1.0-mana-curves.pdf"
