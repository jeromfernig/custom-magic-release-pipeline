import json
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from mtg_pipeline.canonicalize import canonicalize
from mtg_pipeline.config import AppConfig
from mtg_pipeline.exporters.draftmancer import export_draftmancer, pack_layout_errors, sheet_counts
from mtg_pipeline.pipeline import PipelineRunResult
from tests.fixtures import sample_source
from tests.test_ui import APP_PATH, source_mocks


def test_layout_rejects_empty_requested_sheet():
    config = AppConfig()
    assert pack_layout_errors(config.draft.booster_layout, {"Common": 82, "Uncommon": 96}) == [
        "Default: RareMythic is requested but contains no cards."
    ]


@pytest.mark.parametrize("settings", [
    {}, {"layouts": {"Default": {"weight": 1, "slots": {}}}},
    {"layouts": {"Default": {"weight": 1, "slots": {"Common": -1}}}},
    {"layouts": {"Default": {"weight": True, "slots": {"Common": 10}}}},
    {"layouts": {"Default": {"weight": 1, "slots": {"Unknown": 1}}}},
    {"layouts": {"Default": {"weight": 1, "slots": [{"name": "Any", "count": 1, "sheets": [{"name": [], "weight": 1}]}]}}},
])
def test_invalid_layouts(settings):
    assert pack_layout_errors(settings)


def test_weighted_layout_validation_and_export(tmp_path):
    config = AppConfig()
    config.draft.booster_layout = {"withReplacement": True, "layouts": {"Mixed": {
        "weight": 1, "slots": [{"name": "RareOrMythic", "count": 1, "sheets": [
            {"name": "Rare", "weight": 7}, {"name": "Mythic", "weight": 1},
        ]}, {"name": "Common", "count": 10}],
    }}}
    set_def, cards = canonicalize(sample_source(), config)
    assert pack_layout_errors(config.draft.booster_layout, sheet_counts(cards)) == []
    text = export_draftmancer(set_def, cards, config, tmp_path / "draft.txt").read_text(encoding="utf-8")
    settings = json.loads(text.split("[Settings]\n")[1].split("\n[Common]")[0])
    assert settings == config.draft.booster_layout


def test_ui_layout_edits_reach_pipeline_and_survive_reruns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with (
        source_mocks(sample_source()),
        patch("mtg_pipeline.artwork.r2.r2_settings", return_value=("bucket", "https://cdn.example", True)),
        patch("mtg_pipeline.pipeline.PipelineRunner.run", return_value=PipelineRunResult()) as run,
        patch("mtg_pipeline.steps.finalize_manifest", return_value=None),
    ):
        app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
        assert not app.exception
        app.number_input(key="draft_editor_count_Common").set_value(9)
        app.number_input(key="draft_editor_count_Uncommon").set_value(6)
        app.number_input(key="draft_editor_count_RareMythic").set_value(0)
        app.checkbox(key="draft_editor_replacement").check().run()
        app.checkbox(key="step_rendered_card_r2_upload").check().run()
        app.checkbox(key="step_draftmancer").check().run()
        app.run()
        assert app.number_input(key="draft_editor_count_Common").value == 9
        next(button for button in app.button if button.label == "Run selected steps").click().run()
        assert not app.exception
        context = run.call_args.args[0]
        settings = context.config.draft.booster_layout
        assert settings["layouts"]["Default"]["slots"] == {"Common": 9, "Uncommon": 6}
        assert settings["withReplacement"] is True
        app.radio(key="draft_editor_mode").set_value("Advanced JSON").run()
        assert json.loads(app.text_area(key="draft_editor_json").value) == settings


def test_ui_empty_sheet_blocks_pack_dependent_steps(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = sample_source()
    source.card_rows = source.card_rows[:2]  # Only commons.
    with source_mocks(source), patch("mtg_pipeline.artwork.r2.r2_settings", return_value=("bucket", "https://cdn.example", True)):
        app = AppTest.from_file(str(APP_PATH), default_timeout=15).run()
        assert any("RareMythic is requested but contains no cards" in warning.value for warning in app.warning)
        assert next(button for button in app.button if button.label == "Run selected steps").disabled
        app.checkbox(key="step_mana_curve").uncheck().run()
        assert not next(button for button in app.button if button.label == "Run selected steps").disabled
        app.checkbox(key="step_rendered_card_r2_upload").check().run()
        app.checkbox(key="step_draftmancer").check().run()
        assert next(button for button in app.button if button.label == "Run selected steps").disabled
        app.number_input(key="draft_editor_count_Uncommon").set_value(0)
        app.number_input(key="draft_editor_count_RareMythic").set_value(0).run()
        assert not next(button for button in app.button if button.label == "Run selected steps").disabled
        app.radio(key="draft_editor_mode").set_value("Advanced JSON").run()
        app.text_area(key="draft_editor_json").set_value("invalid JSON").run()
        assert next(button for button in app.button if button.label == "Run selected steps").disabled
        assert not app.exception
