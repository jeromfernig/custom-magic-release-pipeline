from __future__ import annotations

import shutil
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from mtg_pipeline.artwork.openai_images import (
    OpenAIImageGenerator,
    is_moderation_blocked,
    moderation_blocked_message,
    moderation_stage,
)
from mtg_pipeline.config import AppConfig
from mtg_pipeline.models import CardDefinition
from mtg_pipeline.utils.filenames import slugify, with_suffix_for_content_type


@dataclass(frozen=True)
class GeneratedArtworkUpdate:
    row_number: int
    artwork_path: str
    artist_credit: str


@dataclass(frozen=True)
class ArtworkPreparationResult:
    output_files: list[Path]
    generated_updates: list[GeneratedArtworkUpdate]
    warnings: list[str]


class ArtworkPreparer:
    def __init__(self, config: AppConfig, project_root: Path, timeout_seconds: int = 30, progress: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.project_root = project_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.progress = progress or (lambda message: None)

    def prepare(self, cards: list[CardDefinition], set_id: str = "set") -> ArtworkPreparationResult:
        cache_dir = (self.project_root / self.config.paths.artwork_root / slugify(set_id)).resolve()
        release_root = (self.project_root / self.config.paths.release_root).resolve()
        if not cache_dir.is_relative_to(self.project_root) or cache_dir.is_relative_to(release_root):
            raise ValueError("Artwork storage must be inside the project and outside the release folder.")
        cache_dir.mkdir(parents=True, exist_ok=True)
        output_files: list[Path] = []
        generated_updates: list[GeneratedArtworkUpdate] = []
        warnings: list[str] = []
        generator = OpenAIImageGenerator(
            model=self.config.images.generation_model,
            size=self.config.images.generation_size,
            quality=self.config.images.generation_quality,
        )

        for index, card in enumerate(cards, start=1):
            label = f"Artwork {index}/{len(cards)}: {card.name}"
            if not card.artwork_url and not card.art_prompt:
                self.progress(f"{label} - creating missing-art placeholder")
                message = "no artwork_url or art_prompt specified in sheet"
                output_files.append(self._prepare_placeholder(cache_dir, card, message))
                warnings.append(f"Using placeholder artwork for {card.collector_number} - {card.name}: {message}")
                continue

            errors: list[str] = []
            prepared = False
            for attempt in range(1, 2 + self.config.images.generation_retry_count):
                self.progress(f"{label} - attempt {attempt}/{1 + self.config.images.generation_retry_count}")
                try:
                    output_path, update = self._prepare_card(card, generator, cache_dir)
                except Exception as exc:
                    errors.append(_artwork_error_message(exc))
                    self.progress(f"{label} - attempt failed: {errors[-1]}")
                    if is_moderation_blocked(exc) and moderation_stage(exc) == "input":
                        break
                else:
                    prepared = True
                    output_files.append(output_path)
                    if update:
                        generated_updates.append(update)
                    if errors:
                        warnings.append(
                            f"Artwork for {card.collector_number} - {card.name} failed on {len(errors)} attempt(s), "
                            f"then succeeded on retry. Last error: {errors[-1]}"
                        )
                    break
            if prepared:
                continue
            message = f"Artwork failed after {len(errors)} attempt(s). {errors[-1]}"
            self.progress(f"{label} - creating error placeholder")
            output_files.append(self._prepare_placeholder(cache_dir, card, message))
            warnings.append(f"Using placeholder artwork for {card.collector_number} - {card.name}: {message}")

        return ArtworkPreparationResult(output_files, generated_updates, warnings)

    def _prepare_card(self, card: CardDefinition, generator: OpenAIImageGenerator, cache_dir: Path) -> tuple[Path, GeneratedArtworkUpdate | None]:
        if card.artwork_url:
            original_url = card.artwork_url
            self.progress(f"Loading artwork: {card.name}")
            output_path = self._prepare_existing_artwork(card, cache_dir)
            update = None
            if card.artwork_url != original_url and card.source_row_number:
                update = GeneratedArtworkUpdate(card.source_row_number, card.artwork_url, card.artist or "")
            return output_path, update
        cached_path = cache_dir / f"{_artwork_stem(card)}.png"
        if not cached_path.exists():
            self.progress(f"Generating artwork: {card.name}")
            generator.generate(_generation_prompt(card, self.config.images.default_art_box_mm), cached_path)
        else:
            self.progress(f"Using cached artwork: {card.name}")
        if not cached_path.is_file():
            raise RuntimeError("Artwork generation did not produce an image file.")
        relative_path = _relative_to_project(cached_path, self.project_root)
        output_path = self._use_artwork(cached_path, card)
        card.artwork_url = relative_path
        card.artist = self.config.images.generated_artist_credit
        update = GeneratedArtworkUpdate(card.source_row_number, relative_path, card.artist) if card.source_row_number else None
        return output_path, update

    def _prepare_placeholder(self, cache_dir: Path, card: CardDefinition, message: str) -> Path:
        # Fallbacks must never become successful cache hits or overwrite source fields in Sheets.
        cached_path = cache_dir / "placeholders" / f"{_artwork_stem(card)}.png"
        _write_placeholder_png(cached_path, card.name, message)
        card.artist = "Pipeline placeholder"
        return self._use_artwork(cached_path, card)

    def _prepare_existing_artwork(self, card: CardDefinition, cache_dir: Path) -> Path:
        parsed = urlparse(card.artwork_url or "")
        if parsed.scheme in {"http", "https"}:
            cached_path = self._download_to_cache(card, cache_dir)
            return self._use_artwork(cached_path, card)
        source_path = (self.project_root / (card.artwork_url or "")).resolve()
        if not source_path.is_relative_to(self.project_root.resolve()):
            raise RuntimeError(f"Local artwork path for {card.collector_number} - {card.name} must stay inside the project root: {card.artwork_url}")
        release_root = (self.project_root / self.config.paths.release_root).resolve()
        artwork_root = (self.project_root / self.config.paths.artwork_root).resolve()
        if not source_path.is_relative_to(artwork_root):
            cached_path = cache_dir / f"{_artwork_stem(card)}{source_path.suffix.lower()}"
            if source_path.is_file():
                shutil.copy2(source_path, cached_path)
            elif not (source_path.is_relative_to(release_root) and cached_path.is_file()):
                raise RuntimeError(f"Local artwork path for {card.name} does not exist: {card.artwork_url}")
            card.artwork_url = _relative_to_project(cached_path, self.project_root)
            return self._use_artwork(cached_path, card)
        if not source_path.is_file():
            raise RuntimeError(f"Local artwork path for {card.name} does not exist: {card.artwork_url}")
        return self._use_artwork(source_path, card)

    def _use_artwork(self, path: Path, card: CardDefinition) -> Path:
        card.artwork_path = _relative_to_project(path, self.project_root)
        card.artwork_filename = path.name
        return path.resolve()

    def _download_to_cache(self, card: CardDefinition, cache_dir: Path) -> Path:
        cached_path = _existing_cached_artwork(cache_dir, card)
        if cached_path:
            return cached_path
        base_path = cache_dir / f"{_artwork_stem(card)}{_image_suffix_from_url(card.artwork_url or '') or '.jpg'}"
        response = requests.get(card.artwork_url, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f"Artwork download failed for {card.collector_number} - {card.name}: HTTP {response.status_code}.")
        path = with_suffix_for_content_type(base_path, response.headers.get("content-type"))
        if not path.exists():
            path.write_bytes(response.content)
        card.artwork_filename = path.name
        return path


def _generation_prompt(card: CardDefinition, default_art_box_mm: str) -> str:
    prompt = card.art_prompt or ""
    prompt_lower = prompt.casefold()
    subtype_hint = " ".join(card.subtypes).strip()
    parts = [prompt]
    if subtype_hint and not any(subtype.casefold() in prompt_lower for subtype in card.subtypes):
        parts.append(f"Primary Pokemon subject: {subtype_hint}.")
    if "mm" not in prompt_lower and "dimension" not in prompt_lower and "aspect" not in prompt_lower:
        parts.append(f"Compose for a Magic-style art box with dimensions {default_art_box_mm}, landscape orientation.")
    parts.append("Create only the illustration artwork, with no card frame, no mana symbols, no text, and no watermark.")
    return "\n".join(part for part in parts if part.strip())


def _artwork_error_message(exc: Exception) -> str:
    if is_moderation_blocked(exc):
        return moderation_blocked_message(exc)
    return f"{type(exc).__name__}: {exc}"


def _existing_cached_artwork(cache_dir: Path, card: CardDefinition) -> Path | None:
    stem = _artwork_stem(card)
    for suffix in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
        path = cache_dir / f"{stem}{suffix}"
        if path.exists():
            return path
    return None


def _artwork_stem(card: CardDefinition) -> str:
    return slugify(card.name)


def _relative_to_project(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _image_suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"} else ""


def _write_placeholder_png(path: Path, card_name: str, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1590, 1170), "white")
    draw = ImageDraw.Draw(image)
    content = f"{card_name}\n\nPlaceholder artwork\n\n{message}"
    for size in range(36, 9, -1):
        font = ImageFont.load_default(size=size)
        lines = []
        for paragraph in content.splitlines():
            lines.extend(textwrap.wrap(paragraph, width=max(20, 1450 // size)) or [""])
        wrapped = "\n".join(lines)
        bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=8)
        if bounds[2] <= 1450 and bounds[3] <= 1030:
            break
    draw.multiline_text((70, 70), wrapped, fill="black", font=font, spacing=8)
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", content)
    image.save(path, pnginfo=metadata)
