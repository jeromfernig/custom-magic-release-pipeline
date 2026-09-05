from __future__ import annotations

import re
from pathlib import Path


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "card"


def safe_card_stem(collector_number: str, name: str) -> str:
    number = slugify(collector_number)
    return f"{number}-{slugify(name)}"


def with_suffix_for_content_type(path: Path, content_type: str | None) -> Path:
    if not content_type:
        return path
    normalized = content_type.split(";")[0].strip().lower()
    suffixes = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
    }
    suffix = suffixes.get(normalized)
    return path.with_suffix(suffix) if suffix else path
