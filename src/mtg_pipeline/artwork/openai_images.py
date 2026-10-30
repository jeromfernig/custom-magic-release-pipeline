from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image

from mtg_pipeline.artwork.generation import log_generation, write_new_image


class ImageGenerationBlocked(RuntimeError):
    """Raised when an image provider rejects generation for safety."""

    def __init__(self, message: str, stage: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage


class OpenAIImageGenerator:
    def __init__(self, model: str, size: str, quality: str) -> None:
        self.model = model
        self.size = size
        self.quality = quality

    def generate(self, prompt: str, output_path: Path) -> Path:
        try:
            return self._generate(prompt, output_path)
        except Exception:
            log_generation("openai", self.model, "error")
            raise

    def _generate(self, prompt: str, output_path: Path) -> Path:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("OpenAI Python SDK is not installed. Run: pip install -e .") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        client = OpenAI()
        try:
            result = client.images.generate(
                model=self.model,
                prompt=prompt,
                size=self.size,
                quality=self.quality,
            )
        except Exception as exc:
            if is_moderation_blocked(exc):
                raise ImageGenerationBlocked(moderation_blocked_message(exc), moderation_stage(exc)) from exc
            raise
        image_base64 = result.data[0].b64_json if result.data else None
        if not image_base64:
            raise RuntimeError("OpenAI image generation did not return image data.")
        image_bytes = base64.b64decode(image_base64, validate=True)
        with Image.open(BytesIO(image_bytes)) as image:
            image.load()
            dimensions = list(image.size)
        path = write_new_image(output_path, image_bytes)
        usage = getattr(result, "usage", None)
        log_generation("openai", self.model, "success", dimensions=dimensions,
                       usage=usage.model_dump(mode="json") if usage else None)
        return path


def _error_body(exc: Exception) -> dict:
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = response.json()
            except (ValueError, TypeError):
                pass
    error = body.get("error", body) if isinstance(body, dict) else {}
    return error if isinstance(error, dict) else {}


def is_moderation_blocked(exc: Exception) -> bool:
    return (
        isinstance(exc, ImageGenerationBlocked)
        or getattr(exc, "code", None) == "moderation_blocked"
        or _error_body(exc).get("code") == "moderation_blocked"
        or "moderation_blocked" in str(exc)
    )


def moderation_stage(exc: Exception) -> str | None:
    if isinstance(exc, ImageGenerationBlocked):
        return exc.stage
    details = _error_body(exc).get("moderation_details")
    return details.get("moderation_stage") if isinstance(details, dict) else None


def moderation_blocked_message(exc: Exception) -> str:
    if isinstance(exc, ImageGenerationBlocked):
        return str(exc)
    error = _error_body(exc)
    message = error.get("message") or "OpenAI image generation was blocked by the safety system."
    details = error.get("moderation_details")
    details = details if isinstance(details, dict) else {}
    parts = [str(message)]
    if stage := details.get("moderation_stage"):
        parts.append(f"Stage: {stage}.")
    categories = details.get("categories")
    if isinstance(categories, list) and categories:
        parts.append(f"Categories: {', '.join(str(category) for category in categories)}.")
    if request_id := getattr(exc, "request_id", None):
        parts.append(f"Request ID: {request_id}.")
    return " ".join(parts)
