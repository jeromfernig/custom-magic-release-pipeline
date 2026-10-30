from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class ImageGenerator(Protocol):
    model: str

    def generate(self, prompt: str, output_path: Path) -> Path: ...


def unused_path(path: Path) -> Path:
    candidate = path
    number = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{number}{path.suffix}")
        number += 1
    return candidate


def write_new_image(path: Path, content: bytes) -> Path:
    """Exclusive creation also protects existing art if another process wins a race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path = unused_path(path)
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        raise
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def safe_error(message: str) -> str:
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if value := os.environ.get(name):
            message = message.replace(value, "[REDACTED]")
    return re.sub(r"(?i)(key=|api_key=|x-goog-api-key[:=]\s*|Bearer\s+)\S+", r"\1[REDACTED]", message)


def log_generation(provider: str, model: str, outcome: str, *, dimensions=None, usage=None) -> None:
    # Never include prompts, raw responses, API exceptions, headers, or credentials.
    logging.getLogger("mtg_pipeline").info("Image generation %s", json.dumps({
        "provider": provider, "model": model, "timestamp": datetime.now(timezone.utc).isoformat(),
        "dimensions": dimensions, "outcome": outcome, "token_usage": usage,
    }, sort_keys=True))
