from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import openai
import pytest
from PIL import Image, ImageChops

from mtg_pipeline.artwork.openai_images import OpenAIImageGenerator
from mtg_pipeline.artwork.preparer import ArtworkPreparer
from mtg_pipeline.canonicalize import canonicalize
from mtg_pipeline.config import AppConfig
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.steps import build_pipeline, finalize_manifest
from tests.fixtures import sample_source


def moderation_error(shape: str = "flat", stage: str = "output") -> openai.BadRequestError:
    error = {
        "message": "Your request was rejected by the safety system.",
        "type": "image_generation_user_error",
        "code": "moderation_blocked",
        "moderation_details": {"moderation_stage": stage, "categories": ["other"]},
    }
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"),
        headers={"x-request-id": "req_regression"}, json={"error": error},
    )
    body = {"flat": error, "nested": {"error": error}, "response": None}[shape]
    return openai.BadRequestError("Error code: 400", response=response, body=body)


def prompted_cards():
    source = sample_source()
    source.card_rows = source.card_rows[:2]
    for row in source.card_rows:
        row["artwork_url"] = ""
        row["art_prompt"] = "A peaceful view over the plains."
    _, cards = canonicalize(source, AppConfig())
    return cards


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (100, 140), "green").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("shape", ["flat", "nested", "response"])
@pytest.mark.parametrize("raw_adapter_error", [False, True])
def test_actual_sdk_moderation_errors_produce_placeholder(tmp_path, monkeypatch, shape, raw_adapter_error):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    card = prompted_cards()[0]
    target = (
        "mtg_pipeline.artwork.preparer.OpenAIImageGenerator.generate"
        if raw_adapter_error else "openai.resources.images.Images.generate"
    )
    with patch(target, side_effect=moderation_error(shape)) as generate:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    assert generate.call_count == 2
    assert len(result.warnings) == 1
    assert "req_regression" in result.warnings[0]
    assert "Stage: output" in result.warnings[0]
    assert result.generated_updates == []
    assert result.artist_updates[0].artist_credit == "moderation error"
    with Image.open(result.output_files[0]) as image:
        image.load()
        assert image.size == (1590, 1170)
        assert ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
        assert "req_regression" in image.info["Description"]


def test_input_block_uses_placeholder_without_retry(tmp_path):
    with patch.object(OpenAIImageGenerator, "generate", side_effect=moderation_error(stage="input")) as generate:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare(prompted_cards()[:1])
    assert generate.call_count == 1
    assert "after 1 attempt(s)" in result.warnings[0]
    assert result.artist_updates[0].artist_credit == "moderation error"


def test_existing_moderation_marker_skips_image_generation(tmp_path):
    card = prompted_cards()[0]
    card.artist = "moderation error"

    with patch.object(OpenAIImageGenerator, "generate") as generate:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])

    generate.assert_not_called()
    assert result.artist_updates == []
    assert "skipped because the sheet marks this prompt" in result.warnings[0]


def test_unrelated_api_error_retries_then_warns(tmp_path):
    error = openai.BadRequestError(
        "Invalid size", response=httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com")),
        body={"code": "invalid_size", "moderation_details": None},
    )
    with patch.object(OpenAIImageGenerator, "generate", side_effect=error) as generate:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare(prompted_cards()[:1])
    assert generate.call_count == 2
    assert "Invalid size" in result.warnings[0]
    with Image.open(result.output_files[0]) as image:
        assert "Invalid size" in image.info["Description"]


@pytest.mark.parametrize("error", [moderation_error(), RuntimeError("Image service unavailable")])
def test_blocked_card_does_not_stop_rendering_or_exports(tmp_path, monkeypatch, caplog, error):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    source = sample_source()
    source.card_rows = source.card_rows[:2]
    for row in source.card_rows:
        row["artwork_url"] = ""
        row["art_prompt"] = "A peaceful view over the plains."
    success = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes()).decode())])
    uploads = []
    progress = []

    def render(params):
        assert progress[-1].startswith("Rendering ")
        assert params["artwork"].startswith("https://cdn.example/")
        uploads.append(params["artwork"])
        buffer = BytesIO()
        Image.new("RGB", (100, 140), "green").save(buffer, format="JPEG")
        return buffer.getvalue()

    with (
        patch("mtg_pipeline.steps.GoogleSheetsSource.load", return_value=source),
        patch("mtg_pipeline.steps.GoogleSheetsSource.update_generated_artwork_fields") as update,
        patch("mtg_pipeline.steps.GoogleSheetsSource.update_artist_fields") as update_artist,
        patch("openai.resources.images.Images.generate", side_effect=[error, error, success]) as generate,
        patch("mtg_pipeline.renderers.mtg_design.MtgDesignRendererClient.render", side_effect=render),
        patch("mtg_pipeline.artwork.r2.CloudflareR2Client.__init__", return_value=None),
        patch("mtg_pipeline.artwork.r2.CloudflareR2Client.upload_file", return_value=True),
        patch("mtg_pipeline.artwork.r2.CloudflareR2Client.public_url", side_effect=lambda key: "https://cdn.example/" + key),
        caplog.at_level(logging.INFO, logger="mtg_pipeline"),
    ):
        config = AppConfig()
        config.rendering.engine = "mtg_design"
        config.draft.booster_layout = {"layouts": {"Default": {"weight": 1, "slots": {"Common": 1}}}}
        context = PipelineContext(config=config, progress_callback=progress.append)
        runner = build_pipeline(config)
        selected = {step.id for step in runner.steps if step.poc_available}
        result = runner.run(context, selected)
        manifest_path = finalize_manifest(context, selected, result)

    assert generate.call_count == 3
    assert update.call_count == 1
    if isinstance(error, openai.BadRequestError):
        update_artist.assert_called_once_with("Cards", config.columns, [(3, "moderation error")])
    else:
        update_artist.assert_not_called()
    assert len(uploads) == 2
    assert all(result.steps[step].status == "success" for step in selected)
    assert "Using placeholder artwork" in caplog.text
    manifest = json.loads(manifest_path.read_text())
    assert manifest["warnings"] == result.warnings
    saved_cards = json.loads((context.release_dir / "data/cards.json").read_text())
    assert saved_cards[0]["artwork_path"].startswith(".cache/artwork/")
    assert saved_cards[0]["artwork_url"] is None
    assert saved_cards[1]["artwork_url"].endswith("verdant-seedling.png")
    pdf = context.release_dir / "print/pkm-0.3.0.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert not (context.release_dir / "artwork").exists()
    assert all((tmp_path / card.artwork_path).is_relative_to(tmp_path / ".cache/artwork") for card in context.cards)
    assert any(message.startswith("Step 4/14: Prepare artwork") for message in progress)
    assert any("Artwork 1/2:" in message for message in progress)
    assert any("creating error placeholder" in message for message in progress)
    assert any(message.startswith("Saving artwork reference") for message in progress)


def test_placeholder_does_not_prevent_generation_next_run(tmp_path):
    preparer = ArtworkPreparer(AppConfig(), tmp_path)
    with patch.object(OpenAIImageGenerator, "generate", side_effect=moderation_error()):
        preparer.prepare(prompted_cards()[:1])

    def generate(prompt, output_path):
        output_path.write_bytes(png_bytes())
        return output_path

    with patch.object(OpenAIImageGenerator, "generate", side_effect=generate) as generate_mock:
        result = preparer.prepare(prompted_cards()[:1])
    assert generate_mock.call_count == 1
    assert result.warnings == []
    assert result.generated_updates[0].artwork_path == "artwork/set/static-burst.png"
    assert result.output_files[0].read_bytes() == png_bytes()


def test_missing_art_uses_numbered_shared_placeholders_in_card_order(tmp_path):
    cards = prompted_cards()
    for card in cards:
        card.name = "Bulbasaur"
        card.subtypes = ["Bulbasaur"]
        card.artwork_url = None
        card.art_prompt = None
    placeholders = tmp_path / "artwork/placeholders"
    placeholders.mkdir(parents=True)
    (placeholders / "bulbasaur1.webp").write_bytes(b"first")
    (placeholders / "bulbasaur2.webp").write_bytes(b"second")

    result = ArtworkPreparer(AppConfig(), tmp_path).prepare(cards)

    assert [path.name for path in result.output_files] == ["bulbasaur1.webp", "bulbasaur2.webp"]
    assert [card.artwork_path for card in cards] == [
        "artwork/placeholders/bulbasaur1.webp",
        "artwork/placeholders/bulbasaur2.webp",
    ]


def test_changed_manual_art_is_used_without_a_release_copy(tmp_path):
    source = tmp_path / "artwork/manual.png"
    source.parent.mkdir()
    source.write_bytes(b"original")
    card = prompted_cards()[0]
    card.artwork_url = "artwork/manual.png"
    preparer = ArtworkPreparer(AppConfig(), tmp_path)
    result = preparer.prepare([card])
    source.write_bytes(b"replacement")
    preparer.prepare([card])
    assert result.output_files[0].read_bytes() == b"replacement"


@pytest.mark.parametrize("retry_count", [0, 2])
def test_retry_budget_and_successful_card_before_failure(tmp_path, retry_count):
    cards = prompted_cards()
    cards[0].artwork_url = "artwork/manual.png"
    (tmp_path / "artwork").mkdir()
    (tmp_path / cards[0].artwork_url).write_bytes(png_bytes())
    config = AppConfig()
    config.images.generation_retry_count = retry_count
    with patch.object(OpenAIImageGenerator, "generate", side_effect=TimeoutError("Request timed out")) as generate:
        result = ArtworkPreparer(config, tmp_path).prepare(cards)
    assert generate.call_count == retry_count + 1
    assert len(result.output_files) == 2
    assert len(result.warnings) == 1
    assert cards[1].artwork_path.startswith("artwork/set/placeholders/")
    with Image.open(result.output_files[1]) as image:
        assert "Request timed out" in image.info["Description"]
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getextrema() == ((0, 255), (0, 255), (0, 255))


def test_missing_local_art_does_not_stop_later_cards(tmp_path):
    cards = prompted_cards()
    cards[0].artwork_url = "artwork/nonexistent.png"
    cards[1].art_prompt = None
    with patch.object(OpenAIImageGenerator, "generate") as generate:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare(cards)
    generate.assert_not_called()
    assert len(result.output_files) == 2
    assert "does not exist" in result.warnings[0]
    assert "no artwork_url or art_prompt specified in sheet" in result.warnings[1]
    assert result.generated_updates == []


def test_download_error_retries_then_uses_placeholder(tmp_path):
    card = prompted_cards()[0]
    card.artwork_url = "https://example.com/missing.png"
    with patch("mtg_pipeline.artwork.preparer.requests.get", return_value=Mock(status_code=404)) as get:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card])
    assert get.call_count == 2
    assert "HTTP 404" in result.warnings[0]
    assert result.output_files[0].is_file()


def test_old_numbered_release_art_migrates_to_stable_cache(tmp_path):
    card = prompted_cards()[0]
    card.name = "Helping Hand"
    card.artwork_url = "releases/pokemon-mtg-set/0.1.0/artwork/003-helping-hand.png"
    source_path = tmp_path / card.artwork_url
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(png_bytes())
    with patch.object(OpenAIImageGenerator, "generate") as generate:
        result = ArtworkPreparer(AppConfig(), tmp_path).prepare(
            [card], "pokemon-mtg-set",
        )
    generate.assert_not_called()
    assert result.warnings == []
    assert source_path.is_file()
    assert card.artwork_url == "artwork/pokemon-mtg-set/helping-hand.png"
    assert (tmp_path / card.artwork_url).read_bytes() == png_bytes()
    assert result.output_files[0].name == "helping-hand.png"
    assert result.generated_updates[0].artwork_path == card.artwork_url


def test_artwork_paths_use_project_root_not_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    card = prompted_cards()[0]

    def generate(prompt, path):
        path.write_bytes(png_bytes())
        return path

    with patch.object(OpenAIImageGenerator, "generate", side_effect=generate):
        result = ArtworkPreparer(AppConfig(), project).prepare([card])
    assert result.warnings == []
    assert result.output_files[0] == project / "artwork/set/static-burst.png"
    assert card.artwork_url == "artwork/set/static-burst.png"
    assert not (other / "artwork").exists()
    assert not (project / "releases").exists()


def test_shared_manual_art_is_not_duplicated(tmp_path):
    source = tmp_path / "artwork/shared.png"
    source.parent.mkdir()
    source.write_bytes(png_bytes())
    cards = prompted_cards()
    for card in cards:
        card.artwork_url = "artwork/shared.png"
    result = ArtworkPreparer(AppConfig(), tmp_path).prepare(cards)
    assert result.output_files == [source, source]
    assert all(card.artwork_path == "artwork/shared.png" for card in cards)
    assert len(list((tmp_path / "artwork").rglob("*.png"))) == 1
    assert not (tmp_path / "releases").exists()


def test_removed_legacy_release_copy_resolves_to_preserved_art(tmp_path):
    card = prompted_cards()[0]
    card.name = "Helping Hand"
    card.artwork_url = "releases/pokemon-mtg-set/0.1.0/artwork/003-helping-hand.png"
    preserved = tmp_path / "artwork/pokemon-mtg-set/helping-hand.png"
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(png_bytes())
    result = ArtworkPreparer(AppConfig(), tmp_path).prepare([card], "pokemon-mtg-set")
    assert result.warnings == []
    assert result.output_files == [preserved]
    assert card.artwork_path == "artwork/pokemon-mtg-set/helping-hand.png"
    assert result.generated_updates[0].artwork_path == card.artwork_path
