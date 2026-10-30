from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from mtg_pipeline.artwork.downloader import ArtworkDownloader
from mtg_pipeline.artwork.openai_images import ImageGenerationBlocked
from mtg_pipeline.artwork.preparer import ArtworkPreparer, _generation_prompt
from mtg_pipeline.config import AppConfig, SourceConfig
from mtg_pipeline.sources.google_sheets import GoogleSheetsSource, _callback_params, _callback_state, _column_letter, _is_invalid_scope_refresh_error, _is_oauth_callback_path, _parse_set_values, _parse_table_values, allow_localhost_oauth_transport
from mtg_pipeline.sources.sheet_history import format_sheet_option, load_sheet_history, normalize_spreadsheet_id, parse_sheet_option, save_sheet_history, sheet_options

from tests.fixtures import sample_source
from mtg_pipeline.canonicalize import canonicalize


def test_google_sheet_parsers() -> None:
    set_values = [["key", "value"], ["set_id", "test-set"], ["set_name", "Test Set"], ["set_code", "TST"], ["version", "0.1.0"]]
    card_values = [["id", "name"], ["1", "Alpha"]]
    assert _parse_set_values(set_values)["set_name"] == "Test Set"
    assert _parse_table_values(card_values) == [{"id": "1", "name": "Alpha", "__row_number": 2}]


def test_column_letter() -> None:
    assert _column_letter(1) == "A"
    assert _column_letter(27) == "AA"


def test_localhost_oauth_transport_flag(monkeypatch) -> None:
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    allow_localhost_oauth_transport()
    assert os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"


def test_callback_state_extraction() -> None:
    assert _callback_state("http://localhost:1234/?code=abc&state=xyz") == "xyz"


def test_callback_params_extraction() -> None:
    assert _callback_params("http://localhost:1234/?code=abc&state=xyz") == {"code": "abc", "state": "xyz"}


def test_oauth_callback_path_detection_ignores_browser_noise() -> None:
    assert _is_oauth_callback_path("/?code=abc&state=xyz")
    assert _is_oauth_callback_path("/?error=access_denied")
    assert not _is_oauth_callback_path("/")
    assert not _is_oauth_callback_path("/favicon.ico")


def test_invalid_scope_refresh_error_detection() -> None:
    assert _is_invalid_scope_refresh_error(Exception("('invalid_scope: Bad Request', {'error': 'invalid_scope'})"))


def test_token_status_detects_valid_token(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    _write_token(token_path, "2999-01-01T00:00:00Z", ["https://www.googleapis.com/auth/spreadsheets"])
    status = GoogleSheetsSource(SourceConfig(token_path=token_path)).token_status()
    assert status.state == "valid"


def test_token_status_detects_expired_token(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    _write_token(token_path, "2000-01-01T00:00:00Z", ["https://www.googleapis.com/auth/spreadsheets"])
    status = GoogleSheetsSource(SourceConfig(token_path=token_path)).token_status()
    assert status.state == "expired"


def test_token_status_detects_missing_scope(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    _write_token(token_path, "2999-01-01T00:00:00Z", ["https://www.googleapis.com/auth/spreadsheets.readonly"])
    status = GoogleSheetsSource(SourceConfig(token_path=token_path)).token_status()
    assert status.state == "missing_scope"


def test_sheet_history_format_parse_and_save(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    sheet_id = "1AbC_def-Ghi1234567890"
    save_sheet_history(path, sheet_id, "mtg_pokemon_set")
    history = load_sheet_history(path)
    assert history == {sheet_id: "mtg_pokemon_set"}
    option = format_sheet_option(sheet_id, "mtg_pokemon_set")
    assert option == f"{sheet_id} (mtg_pokemon_set)"
    assert parse_sheet_option(option) == sheet_id
    assert sheet_options(history) == [option]


def _write_token(path: Path, expiry: str, scopes: list[str]) -> None:
    path.write_text(json.dumps({
        "token": "access-token",
        "refresh_token": "refresh-token",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id.apps.googleusercontent.com",
        "client_secret": "client-secret",
        "scopes": scopes,
        "expiry": expiry,
    }), encoding="utf-8")


def test_normalize_spreadsheet_id_accepts_full_url() -> None:
    url = "https://docs.google.com/spreadsheets/d/1AbC_def-Ghi123/edit#gid=875508514"
    assert normalize_spreadsheet_id(url) == "1AbC_def-Ghi123"


def test_normalize_spreadsheet_id_rejects_short_numeric_gid() -> None:
    try:
        normalize_spreadsheet_id("875508514")
    except ValueError as exc:
        assert "tab gid" in str(exc)
    else:
        raise AssertionError("Expected short numeric gid to be rejected")


def test_artwork_downloader_mocked(tmp_path: Path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    _, cards = canonicalize(sample_source(), config)
    card = next(card for card in cards if card.name == "Verdant Seedling")
    response = Mock(status_code=200, content=b"image", headers={"content-type": "image/png"})
    with patch("mtg_pipeline.artwork.downloader.requests.get", return_value=response) as get:
        paths = ArtworkDownloader().download([card], tmp_path)
    assert get.called
    assert paths[0].name == "004-verdant-seedling.png"
    assert paths[0].read_bytes() == b"image"


def test_artwork_preparer_generates_from_prompt_and_returns_sheet_update(tmp_path: Path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    source = sample_source()
    source.card_rows[0]["artwork_url"] = ""
    source.card_rows[0]["art_prompt"] = "A small creature in a forest."
    _, cards = canonicalize(source, config)
    card = next(card for card in cards if card.name == "Verdant Seedling")
    with patch("mtg_pipeline.artwork.preparer.OpenAIImageGenerator.generate") as generate:
        generate.side_effect = lambda prompt, output_path: output_path.write_bytes(b"png") or output_path
        result = ArtworkPreparer(config, tmp_path).prepare([card], "pokemon-mtg-set")
    assert result.output_files[0].name == "verdant-seedling.png"
    assert (tmp_path / "artwork" / "pokemon-mtg-set" / "verdant-seedling.png").exists()
    assert result.generated_updates[0].artwork_path == "artwork/pokemon-mtg-set/verdant-seedling.png"
    assert result.generated_updates[0].artist_credit == "OpenAI gpt-image-2.5-sunburst"
    assert card.artwork_url == result.generated_updates[0].artwork_path
    assert card.artist == "OpenAI gpt-image-2.5-sunburst"
    assert result.warnings == []


def test_artwork_preparer_uses_manual_root_artwork_directly(tmp_path: Path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    manual_path = tmp_path / "artwork" / "pokemon-mtg-set" / "verdant-seedling.png"
    manual_path.parent.mkdir(parents=True)
    manual_path.write_bytes(b"manual-art")
    source = sample_source()
    source.card_rows[0]["artwork_url"] = "artwork/pokemon-mtg-set/verdant-seedling.png"
    _, cards = canonicalize(source, config)
    card = next(card for card in cards if card.name == "Verdant Seedling")

    result = ArtworkPreparer(config, tmp_path).prepare([card], "pokemon-mtg-set")

    assert result.output_files[0].name == "verdant-seedling.png"
    assert result.output_files[0].read_bytes() == b"manual-art"
    assert card.artwork_url == "artwork/pokemon-mtg-set/verdant-seedling.png"
    assert card.artwork_filename == "verdant-seedling.png"


def test_artwork_preparer_accepts_relative_source_artwork_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    source_path = tmp_path / "artwork" / "pokemon-mtg-set" / "helping-hand.png"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"manual-art")
    source = sample_source()
    source.card_rows[0]["name"] = "Helping Hand"
    source.card_rows[0]["artwork_url"] = "artwork/pokemon-mtg-set/helping-hand.png"
    _, cards = canonicalize(source, config)
    card = next(card for card in cards if card.name == "Helping Hand")

    result = ArtworkPreparer(config, Path.cwd()).prepare([card], "pokemon-mtg-set")

    assert result.output_files[0] == tmp_path / "artwork" / "pokemon-mtg-set" / "helping-hand.png"
    assert result.output_files[0].read_bytes() == b"manual-art"


def test_artwork_preparer_retries_blocked_generation(tmp_path: Path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    source = sample_source()
    source.card_rows[0]["artwork_url"] = ""
    source.card_rows[0]["art_prompt"] = "A strong Machop looking out over the plains."
    _, cards = canonicalize(source, config)
    card = next(card for card in cards if card.name == "Verdant Seedling")
    prompts: list[str] = []

    def generate(prompt: str, output_path: Path) -> Path:
        prompts.append(prompt)
        if len(prompts) == 1:
            raise ImageGenerationBlocked("blocked by safety")
        output_path.write_bytes(b"png")
        return output_path

    with patch("mtg_pipeline.artwork.preparer.OpenAIImageGenerator.generate", side_effect=generate):
        result = ArtworkPreparer(config, tmp_path).prepare([card], "pokemon-mtg-set")

    assert len(prompts) == 2
    assert prompts[1] == prompts[0]
    assert result.output_files[0].exists()
    assert card.artwork_url == "artwork/pokemon-mtg-set/verdant-seedling.png"
    assert "succeeded on retry" in result.warnings[0]


def test_artwork_preparer_warns_when_generation_remains_blocked(tmp_path: Path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    source = sample_source()
    source.card_rows[0]["artwork_url"] = ""
    source.card_rows[0]["art_prompt"] = "A strong Machop looking out over the plains."
    _, cards = canonicalize(source, config)
    card = next(card for card in cards if card.name == "Verdant Seedling")

    with patch("mtg_pipeline.artwork.preparer.OpenAIImageGenerator.generate", side_effect=ImageGenerationBlocked("blocked by safety")):
        result = ArtworkPreparer(config, tmp_path).prepare([card], "pokemon-mtg-set")

    assert result.output_files[0].name == "verdant-seedling.png"
    with Image.open(result.output_files[0]) as image:
        assert image.format == "PNG"
        assert "blocked by safety" in image.info["Description"]
    assert result.generated_updates == []
    assert card.artwork_url is None
    assert card.artwork_path == "artwork/pokemon-mtg-set/placeholders/verdant-seedling.png"
    assert "Artwork failed after 2 attempt(s)" in result.warnings[0]
    assert "Using placeholder artwork" in result.warnings[0]


def test_artwork_preparer_uses_placeholder_when_art_source_missing(tmp_path: Path) -> None:
    config = AppConfig()
    config.render_profiles["fullart_colorless"] = config.render_profiles["standard"]
    source = sample_source()
    source.card_rows[0]["artwork_url"] = ""
    source.card_rows[0]["art_prompt"] = ""
    _, cards = canonicalize(source, config)
    card = next(card for card in cards if card.name == "Verdant Seedling")

    result = ArtworkPreparer(config, tmp_path).prepare([card], "pokemon-mtg-set")

    assert result.output_files[0].name == "verdant-seedling.png"
    with Image.open(result.output_files[0]) as image:
        assert "no artwork_url or art_prompt specified in sheet" in image.info["Description"]
    assert result.generated_updates == []
    assert card.artwork_url is None
    assert card.artist == "Pipeline placeholder"


def test_generation_prompt_adds_subtype_and_default_dimensions() -> None:
    config = AppConfig()
    _, cards = canonicalize(sample_source(), config)
    card = next(card for card in cards if card.name == "Verdant Seedling")
    card.art_prompt = "In a forest clearing."
    prompt = _generation_prompt(card, "53 mm x 39 mm")
    assert "Plant" in prompt
    assert "53 mm x 39 mm" in prompt
