from unittest.mock import patch

import pytest
from PIL import Image

from mtg_pipeline.artwork.gemini_images import GeminiImageGenerator
from mtg_pipeline.artwork.openai_images import OpenAIImageGenerator, ImageGenerationBlocked
from mtg_pipeline.artwork.preparer import ArtworkPreparer, _generation_prompt
from mtg_pipeline.config import AppConfig, load_config
from tests.test_artwork_failures import prompted_cards, png_bytes


def blank_card():
    card = prompted_cards()[0]
    card.artist = "  "
    return card


def save_image(prompt, path):
    path.write_bytes(png_bytes())
    return path


def test_default_config_and_example():
    from pathlib import Path
    for config in (AppConfig(), load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")):
        assert config.images.generation_provider == "openai"
        assert config.images.gemini_model == "gemini-3.1-flash-image"
        assert config.images.default_art_folder == "/artwork/pokemon-mtg-set"


def test_blank_artist_generates_before_existing_url_or_cached_art(tmp_path):
    card = blank_card()
    card.artwork_url = "https://example.com/old.png"
    folder = tmp_path / "artwork/pokemon-mtg-set"
    folder.mkdir(parents=True)
    old = folder / "static-burst.png"
    old.write_bytes(b"old")
    with patch.object(OpenAIImageGenerator, "generate", side_effect=save_image) as openai, patch.object(GeminiImageGenerator, "generate") as gemini:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card], "pokemon-mtg-set")
    assert result.output_files[0].name == "static-burst-2.png"
    assert old.read_bytes() == b"old"
    assert card.artist == "OpenAI gpt-image-2.5-sunburst"
    assert result.generated_updates[0].artwork_path.endswith("static-burst-2.png")
    assert card.id == "pkm-002"
    assert openai.call_args.args[0] == _generation_prompt(card, "53 mm x 39 mm")
    gemini.assert_not_called()


def test_fallback_order_and_identical_prompt(tmp_path):
    card = blank_card()
    events = []
    def openai(prompt, path):
        events.append(("openai", prompt))
        raise ImageGenerationBlocked("OpenAI refused", "input")
    def gemini(prompt, path):
        events.append(("gemini", prompt))
        return save_image(prompt, path)
    with patch.object(OpenAIImageGenerator, "generate", side_effect=openai), patch.object(GeminiImageGenerator, "generate", side_effect=gemini):
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    assert [event[0] for event in events] == ["openai", "gemini"]
    assert events[0][1] == events[1][1]
    assert card.artist == "Google gemini-3.1-flash-image"
    assert result.generated_updates[0].artist_credit == card.artist
    assert "OpenAI refused" in result.warnings[0]


def test_legacy_folder_is_third_choice(tmp_path):
    card = blank_card()
    folder = tmp_path / "artwork/pokemon-mtg-set"
    folder.mkdir(parents=True)
    legacy = folder / "Static Burst.JPG"
    legacy.write_bytes(png_bytes())
    with patch.object(OpenAIImageGenerator, "generate", side_effect=RuntimeError("OpenAI down")) as openai, patch.object(GeminiImageGenerator, "generate", side_effect=RuntimeError("Gemini down")) as gemini:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    assert openai.call_count == 2
    gemini.assert_called_once()
    assert result.output_files == [legacy]
    assert card.artist == "OpenAI gpt-image-1"
    assert result.generated_updates[0].artwork_path == "artwork/pokemon-mtg-set/Static Burst.JPG"


def test_all_errors_on_white_placeholder_and_no_secrets(tmp_path, monkeypatch):
    card = blank_card()
    monkeypatch.setenv("GEMINI_API_KEY", "sensitive-test-key")
    with patch.object(OpenAIImageGenerator, "generate", side_effect=[RuntimeError("first OpenAI"), RuntimeError("second OpenAI")]), patch.object(GeminiImageGenerator, "generate", side_effect=RuntimeError("Gemini sensitive-test-key")):
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    with Image.open(result.output_files[0]) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)
        description = image.info["Description"]
        for message in ("first OpenAI", "second OpenAI", "Gemini", "Default art folder"):
            assert message in description
        assert "sensitive-test-key" not in description
    assert result.generated_updates == result.artist_updates == []


def test_explicit_gemini_selection_and_model(tmp_path):
    config = AppConfig()
    config.images.generation_provider = "gemini"
    config.images.gemini_model = "gemini-3-pro-image"
    card = blank_card()
    with patch.object(OpenAIImageGenerator, "generate") as openai, patch.object(GeminiImageGenerator, "generate", side_effect=save_image):
        result = ArtworkPreparer(config, tmp_path).prepare([card])
    openai.assert_not_called()
    assert result.generated_updates[0].artist_credit == "Google gemini-3-pro-image"


def test_nonblank_artist_keeps_existing_art(tmp_path):
    card = prompted_cards()[0]
    path = tmp_path / "artwork/manual.png"
    path.parent.mkdir()
    path.write_bytes(png_bytes())
    card.artwork_url = "artwork/manual.png"
    with patch.object(OpenAIImageGenerator, "generate") as openai, patch.object(GeminiImageGenerator, "generate") as gemini:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    assert result.output_files == [path]
    assert card.artist == "Tester"
    openai.assert_not_called()
    gemini.assert_not_called()


def test_missing_prompt_still_uses_legacy_folder(tmp_path):
    card = blank_card()
    card.art_prompt = None
    folder = tmp_path / "artwork/pokemon-mtg-set"
    folder.mkdir(parents=True)
    art = folder / "static-burst.png"
    art.write_bytes(png_bytes())
    with patch.object(OpenAIImageGenerator, "generate") as openai, patch.object(GeminiImageGenerator, "generate") as gemini:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    assert result.output_files == [art]
    openai.assert_not_called()
    gemini.assert_not_called()


@pytest.mark.parametrize("folder", ["../outside", "releases/art"])
def test_default_folder_cannot_escape_project_or_use_release(tmp_path, folder):
    config = AppConfig()
    config.images.default_art_folder = folder
    card = blank_card()
    card.art_prompt = None
    result = ArtworkPreparer(config, tmp_path).prepare([card])
    assert "Default art folder must be inside" in result.warnings[0]


def test_repeated_placeholders_and_imports_do_not_overwrite(tmp_path):
    config = AppConfig()
    preparer = ArtworkPreparer(config, tmp_path)
    first = blank_card()
    first.art_prompt = None
    result1 = preparer.prepare([first])
    original = result1.output_files[0].read_bytes()
    second = blank_card()
    second.art_prompt = None
    result2 = preparer.prepare([second])
    assert result2.output_files[0].name == "static-burst-2.png"
    assert result1.output_files[0].read_bytes() == original


def test_single_image_cli_requires_opt_in(tmp_path, monkeypatch):
    from mtg_pipeline.smoke_image import main
    monkeypatch.setattr("sys.argv", ["smoke_image", "--prompt-file", str(tmp_path / "prompt.txt")])
    with patch.object(GeminiImageGenerator, "generate") as generate, pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    generate.assert_not_called()
