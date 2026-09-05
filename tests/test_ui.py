from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from mtg_pipeline.sources.google_sheets import TokenStatus
from tests.fixtures import sample_source


APP_PATH = Path(__file__).resolve().parents[1] / "ui/app.py"
POKEMON_SHEET_ID = "10TNw2O_PG9JvWq10ARrYtN0JRjPJceh8rgRPiFbu7G4"


@contextmanager
def source_mocks(source, state="valid"):
    with (
        patch("mtg_pipeline.sources.google_sheets.GoogleSheetsSource.token_status", return_value=TokenStatus(state, state)),
        patch("mtg_pipeline.sources.google_sheets.GoogleSheetsSource.load", return_value=source) as load,
        patch("mtg_pipeline.sources.sheet_history.load_sheet_history", return_value={}),
        patch("mtg_pipeline.sources.sheet_history.save_sheet_history"),
    ):
        yield load


def test_ui_defaults_and_generation_failure_warning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = sample_source()
    source.card_rows = source.card_rows[:1]
    source.card_rows[0]["artwork_url"] = ""
    source.card_rows[0]["art_prompt"] = "A view over the plains."

    with (
        source_mocks(source) as load,
        patch("mtg_pipeline.artwork.preparer.OpenAIImageGenerator.generate", side_effect=RuntimeError("Image service unavailable")) as generate,
    ):
        app = AppTest.from_file(str(APP_PATH)).run()
        assert not app.exception
        assert app.selectbox[0].value == f"{POKEMON_SHEET_ID} (mtg_pokemon_set)"
        assert app.session_state["loaded_source"]["set_metadata"]["set_id"] == "pokemon-mtg-set"
        load.assert_called_once()
        generate.assert_not_called()
        for step in ("print", "draftmancer", "cubecobra", "cockatrice"):
            assert not app.checkbox(key=f"step_{step}").value
        app.checkbox(key="step_rendering").uncheck().run()
        load.assert_called_once()
        next(button for button in app.button if button.label == "Run selected steps").click().run(timeout=15)
        assert generate.call_count == 2

    assert not app.exception
    assert any("Image service unavailable" in warning.value for warning in app.warning)
    assert any(status.label == "Pipeline completed with warnings" for status in app.status)
    assert any("Artwork 1/1:" in block.value and "Generating artwork" in block.value for block in app.code)


def test_failed_autoload_waits_for_manual_retry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with source_mocks(sample_source()) as load:
        load.side_effect = RuntimeError("Sheet temporarily unavailable")
        app = AppTest.from_file(str(APP_PATH)).run()
        assert any("Sheet temporarily unavailable" in error.value for error in app.error)
        app.run()
        load.assert_called_once()
        load.side_effect = None
        next(button for button in app.button if button.label == "Load source").click().run()
        assert load.call_count == 2
        assert "loaded_source" in app.session_state
        assert not app.error


def test_changed_sheet_selection_autoloads_new_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with source_mocks(sample_source()) as load:
        app = AppTest.from_file(str(APP_PATH)).run()
        app.selectbox[0].select("Enter manually").run()
        assert "loaded_source" not in app.session_state
        next(field for field in app.text_input if field.label == "Google Sheet ID").set_value("another-sheet-id").run()
        assert load.call_count == 2


def test_missing_token_does_not_start_interactive_auth_on_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with source_mocks(sample_source(), state="missing") as load:
        app = AppTest.from_file(str(APP_PATH)).run()
        load.assert_not_called()
        assert not app.exception
        assert any(button.label == "Reauthenticate with Google" for button in app.button)
