from __future__ import annotations

import base64
import os
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from mtg_pipeline.artwork.generation import log_generation, write_new_image
from mtg_pipeline.artwork.openai_images import ImageGenerationBlocked


class GeminiGenerationError(RuntimeError):
    pass


def _enum(value) -> str:
    return str(getattr(value, "value", value) or "")


class GeminiImageGenerator:
    def __init__(self, model: str = "gemini-3.1-flash-image", aspect_ratio: str = "3:2",
                 image_size: str = "1K", retry_count: int = 1, timeout_seconds: int = 120) -> None:
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.image_size = image_size
        self.retry_count = retry_count
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str, output_path: Path) -> Path:
        usage = None
        try:
            key = os.environ.get("GEMINI_API_KEY")
            if not key:
                raise GeminiGenerationError("GEMINI_API_KEY is not configured.")
            from google import genai
            from google.genai import types

            # Explicit key and vertexai=False avoid GOOGLE_API_KEY/Vertex AI precedence.
            with genai.Client(api_key=key, vertexai=False, http_options=types.HttpOptions(
                timeout=self.timeout_seconds * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            )) as client:
                errors = []
                for attempt in range(self.retry_count + 1):
                    try:
                        response = client.models.generate_content(
                            model=self.model, contents=prompt,
                            config=types.GenerateContentConfig(
                                response_modalities=["TEXT", "IMAGE"],
                                image_config=types.ImageConfig(aspect_ratio=self.aspect_ratio, image_size=self.image_size),
                            ),
                        )
                        break
                    except Exception as exc:
                        code = getattr(exc, "code", None)
                        errors.append(f"attempt {attempt + 1}: {type(exc).__name__}, status={code or 'unavailable'}")
                        retryable = code in {408, 429, 500, 502, 503, 504} or isinstance(exc, (TimeoutError, ConnectionError))
                        # google-genai uses httpx for transport timeouts and connection failures.
                        import httpx
                        retryable = retryable or isinstance(exc, httpx.TransportError)
                        if not retryable or attempt == self.retry_count:
                            raise GeminiGenerationError(
                                "Gemini request failed (" + "; ".join(errors) + ")."
                            ) from None
                        log_generation("gemini", self.model, f"retryable_error_status_{code}")
                        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
                        try:
                            delay = float(headers.get("retry-after", 2 ** attempt))
                        except (TypeError, ValueError):
                            delay = 2 ** attempt
                        time.sleep(min(30, max(0, delay)))

            metadata = getattr(response, "usage_metadata", None)
            if metadata:
                usage = {name: getattr(metadata, name, None) for name in (
                    "prompt_token_count", "candidates_token_count", "total_token_count", "thoughts_token_count",
                )}
            feedback = getattr(response, "prompt_feedback", None)
            block = _enum(getattr(feedback, "block_reason", None))
            if block and block not in {"BLOCK_REASON_UNSPECIFIED", "BLOCKED_REASON_UNSPECIFIED", "0"}:
                raise ImageGenerationBlocked(f"Gemini prompt rejected: {block}.", "input")
            candidates = getattr(response, "candidates", None) or []
            blocked = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "IMAGE_PROHIBITED_CONTENT", "SPII", "RECITATION", "IMAGE_RECITATION"}
            reasons = [_enum(getattr(candidate, "finish_reason", None)) for candidate in candidates]
            if any(reason in blocked for reason in reasons):
                raise ImageGenerationBlocked(f"Gemini image rejected: {', '.join(reasons)}.", "output")
            for candidate in candidates:
                for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
                    if getattr(part, "thought", False):
                        continue
                    inline = getattr(part, "inline_data", None)
                    if not inline or not (getattr(inline, "mime_type", "") or "").startswith("image/"):
                        continue
                    data = inline.data
                    if isinstance(data, str):
                        data = base64.b64decode(data, validate=True)
                    # Decode fully and normalize to PNG, preserving the pipeline's file contract.
                    with Image.open(BytesIO(data)) as image:
                        image.load()
                        dimensions = list(image.size)
                        buffer = BytesIO()
                        image.convert("RGBA" if "A" in image.getbands() else "RGB").save(buffer, format="PNG")
                    path = write_new_image(output_path, buffer.getvalue())
                    log_generation("gemini", self.model, "success", dimensions=dimensions, usage=usage)
                    return path
            raise GeminiGenerationError(f"Gemini returned no image (finish reasons: {', '.join(reasons) or 'none'}).")
        except Exception as exc:
            log_generation("gemini", self.model, "blocked" if isinstance(exc, ImageGenerationBlocked) else "error", usage=usage)
            if isinstance(exc, (GeminiGenerationError, ImageGenerationBlocked, FileExistsError)):
                raise
            # Raw SDK errors may embed credential-bearing URLs or response text.
            raise GeminiGenerationError(f"Gemini image generation failed ({type(exc).__name__}).") from None
