from __future__ import annotations

import base64
import json
import logging
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from google.genai import types
from PIL import Image

from mtg_pipeline.artwork.gemini_images import GeminiGenerationError, GeminiImageGenerator
from mtg_pipeline.artwork.openai_images import ImageGenerationBlocked


def image_response(fmt="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (1536, 1024), "green").save(buffer, format=fmt)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(parts=[
            types.Part(text="Here is the image"),
            types.Part(inline_data=types.Blob(mime_type=f"image/{fmt.lower()}", data=buffer.getvalue())),
        ]), finish_reason="STOP")],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=12, candidates_token_count=50, total_token_count=62),
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "mock-gemini-secret")
    with patch("google.genai.Client") as constructor:
        mock = constructor.return_value.__enter__.return_value
        yield constructor, mock


@pytest.mark.parametrize("model", ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-3-pro-image"])
def test_image_generation_request_output_and_logging(tmp_path, client, caplog, model):
    constructor, api = client
    api.models.generate_content.return_value = image_response("JPEG")
    with caplog.at_level(logging.INFO, logger="mtg_pipeline"):
        output = GeminiImageGenerator(model).generate("Exact original Pokémon prompt\nKeep this.", tmp_path / "card.png")
    kwargs = api.models.generate_content.call_args.kwargs
    assert kwargs["model"] == model
    assert kwargs["contents"] == "Exact original Pokémon prompt\nKeep this."
    assert kwargs["config"].image_config.aspect_ratio == "3:2"
    assert kwargs["config"].image_config.image_size == "1K"
    assert constructor.call_args.kwargs["api_key"] == "mock-gemini-secret"
    assert constructor.call_args.kwargs["vertexai"] is False
    assert constructor.call_args.kwargs["http_options"].retry_options.attempts == 1
    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.size == (1536, 1024)
    record = json.loads(caplog.records[-1].message.removeprefix("Image generation "))
    assert record["provider"] == "gemini"
    assert record["model"] == model
    assert record["dimensions"] == [1536, 1024]
    assert record["timestamp"].endswith("+00:00")
    assert record["token_usage"]["total_token_count"] == 62
    assert record["outcome"] == "success"
    assert "mock-gemini-secret" not in caplog.text


@pytest.mark.parametrize("response", [types.GenerateContentResponse(), types.GenerateContentResponse(
    candidates=[types.Candidate(content=types.Content(parts=[types.Part(text="text only")]), finish_reason="STOP")])])
def test_missing_image(tmp_path, client, response):
    client[1].models.generate_content.return_value = response
    with pytest.raises(GeminiGenerationError, match="no image"):
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    assert not (tmp_path / "card.png").exists()


@pytest.mark.parametrize("stage", ["input", "output"])
def test_safety_rejection_is_not_retried(tmp_path, client, stage):
    response = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason="SAFETY")
    ) if stage == "input" else types.GenerateContentResponse(candidates=[types.Candidate(finish_reason="IMAGE_SAFETY")])
    client[1].models.generate_content.return_value = response
    with pytest.raises(ImageGenerationBlocked) as error:
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    assert error.value.stage == stage
    assert client[1].models.generate_content.call_count == 1


@pytest.mark.parametrize("code", [429, 500, 503])
def test_transient_error_retries_with_backoff(tmp_path, client, code):
    error = RuntimeError("secret should never be logged")
    error.code = code
    error.response = SimpleNamespace(headers={"retry-after": "3"})
    client[1].models.generate_content.side_effect = [error, image_response()]
    with patch("mtg_pipeline.artwork.gemini_images.time.sleep") as sleep:
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    sleep.assert_called_once_with(3)
    assert client[1].models.generate_content.call_count == 2


@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), ConnectionError("connection")])
def test_transport_retry(tmp_path, client, error):
    client[1].models.generate_content.side_effect = [error, image_response()]
    with patch("mtg_pipeline.artwork.gemini_images.time.sleep"):
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    assert client[1].models.generate_content.call_count == 2


@pytest.mark.parametrize("code, calls", [(401, 1), (403, 1), (404, 1), (429, 2)])
def test_exhausted_or_permanent_errors_are_safe(tmp_path, client, caplog, code, calls):
    error = RuntimeError("mock-gemini-secret in credential-bearing URL")
    error.code = code
    client[1].models.generate_content.side_effect = error
    with patch("mtg_pipeline.artwork.gemini_images.time.sleep"), caplog.at_level(logging.INFO):
        with pytest.raises(GeminiGenerationError) as caught:
            GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    assert str(code) in str(caught.value)
    assert "mock-gemini-secret" not in str(caught.value) + caplog.text
    assert client[1].models.generate_content.call_count == calls


def test_missing_key_does_not_make_request(tmp_path, client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(GeminiGenerationError, match="GEMINI_API_KEY"):
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    client[0].assert_not_called()


def test_invalid_image_and_thought_images_are_not_saved(tmp_path, client):
    response = image_response()
    response.candidates[0].content.parts[-1].inline_data.data = b"not an image"
    client[1].models.generate_content.return_value = response
    with pytest.raises(GeminiGenerationError):
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")
    assert not (tmp_path / "card.png").exists()
    response.candidates[0].content.parts[-1].thought = True
    with pytest.raises(GeminiGenerationError, match="no image"):
        GeminiImageGenerator().generate("prompt", tmp_path / "card.png")


def test_provider_never_overwrites(tmp_path, client):
    original = tmp_path / "card.png"
    original.write_bytes(b"original")
    client[1].models.generate_content.return_value = image_response()
    output = GeminiImageGenerator().generate("prompt", original)
    assert output.name == "card-2.png"
    assert original.read_bytes() == b"original"
