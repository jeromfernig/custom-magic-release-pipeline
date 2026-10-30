from __future__ import annotations

import json
import re
import textwrap
from io import BytesIO
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
from mtg_pipeline.artwork.gemini_images import GeminiImageGenerator
from mtg_pipeline.artwork.generation import ImageGenerator, safe_error, unused_path, write_new_image
from mtg_pipeline.config import AppConfig
from mtg_pipeline.models import CardDefinition
from mtg_pipeline.utils.filenames import slugify, with_suffix_for_content_type
from mtg_pipeline.utils.cache import write_json


@dataclass(frozen=True)
class GeneratedArtworkUpdate:
    row_number: int
    artwork_path: str
    artist_credit: str


@dataclass(frozen=True)
class ArtistCreditUpdate:
    row_number: int
    artist_credit: str


@dataclass(frozen=True)
class ArtworkPreparationResult:
    output_files: list[Path]
    generated_updates: list[GeneratedArtworkUpdate]
    artist_updates: list[ArtistCreditUpdate]
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
        artist_updates: list[ArtistCreditUpdate] = []
        warnings: list[str] = []
        shared_placeholder_offsets: dict[str, int] = {}
        generator = OpenAIImageGenerator(
            model=self.config.images.generation_model,
            size=self.config.images.generation_size,
            quality=self.config.images.generation_quality,
        )

        for index, card in enumerate(cards, start=1):
            label = f"Artwork {index}/{len(cards)}: {card.name}"
            if not (card.artist or "").strip():
                output_path, update, card_warnings = self._prepare_uncredited(card, generator, cache_dir, label)
                output_files.append(output_path)
                if update:
                    generated_updates.append(update)
                warnings.extend(card_warnings)
                continue
            if not card.artwork_url and card.art_prompt and (card.artist or "").strip().casefold() == "moderation error":
                message = "art generation skipped because the sheet marks this prompt with moderation error"
                self.progress(f"{label} - skipping previously moderated prompt")
                output_files.append(self._prepare_placeholder(cache_dir, card, message))
                warnings.append(f"Using placeholder artwork for {card.collector_number} - {card.name}: {message}")
                continue
            if not card.artwork_url and not card.art_prompt:
                message = "no artwork_url or art_prompt specified in sheet"
                shared_placeholder = self._next_shared_placeholder(card, shared_placeholder_offsets)
                if shared_placeholder:
                    self.progress(f"{label} - using shared placeholder: {shared_placeholder.name}")
                    output_files.append(self._use_artwork(shared_placeholder, card))
                    card.artist = "Pipeline placeholder"
                    warnings.append(
                        f"Using shared placeholder artwork for {card.collector_number} - {card.name}: "
                        f"{shared_placeholder.name}"
                    )
                else:
                    self.progress(f"{label} - creating missing-art placeholder")
                    output_files.append(self._prepare_placeholder(cache_dir, card, message))
                    warnings.append(f"Using placeholder artwork for {card.collector_number} - {card.name}: {message}")
                continue

            errors: list[str] = []
            moderation_errors: list[bool] = []
            prepared = False
            selected = self._gemini_generator() if self.config.images.generation_provider == "gemini" else generator
            attempts = 1 if isinstance(selected, GeminiImageGenerator) and not card.artwork_url else 1 + self.config.images.generation_retry_count
            for attempt in range(1, attempts + 1):
                self.progress(f"{label} - attempt {attempt}/{attempts}")
                try:
                    output_path, update = self._prepare_card(card, selected, cache_dir)
                except Exception as exc:
                    errors.append(_artwork_error_message(exc))
                    moderation_errors.append(is_moderation_blocked(exc))
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
            message = f"Artwork failed after {len(errors)} attempt(s). " + "\n".join(errors)
            self.progress(f"{label} - creating error placeholder")
            output_files.append(self._prepare_placeholder(cache_dir, card, message))
            warnings.append(f"Using placeholder artwork for {card.collector_number} - {card.name}: {message}")
            if moderation_errors and all(moderation_errors) and card.source_row_number:
                card.artist = "moderation error"
                artist_updates.append(ArtistCreditUpdate(card.source_row_number, card.artist))

        return ArtworkPreparationResult(output_files, generated_updates, artist_updates, warnings)

    def _gemini_generator(self) -> GeminiImageGenerator:
        images = self.config.images
        return GeminiImageGenerator(images.gemini_model, images.gemini_aspect_ratio,
                                    images.gemini_image_size, images.generation_retry_count,
                                    images.generation_timeout_seconds)

    def _prepare_uncredited(self, card: CardDefinition, openai_generator: ImageGenerator, cache_dir: Path,
                            label: str) -> tuple[Path, GeneratedArtworkUpdate | None, list[str]]:
        errors: list[str] = []
        providers = []
        if self.config.images.generation_provider == "openai":
            providers.append(("OpenAI", openai_generator, self.config.images.generated_artist_credit))
        providers.append(("Gemini", self._gemini_generator(), f"Google {self.config.images.gemini_model}"))
        for provider, generator, credit in providers:
            # Gemini owns classified retries and backoff; preserve the OpenAI retry budget.
            attempts = 1 if provider == "Gemini" else 1 + self.config.images.generation_retry_count
            for attempt in range(1, attempts + 1):
                self.progress(f"{label} - Generating artwork with {provider} {generator.model}, attempt {attempt}/{attempts}")
                try:
                    if not (card.art_prompt or "").strip():
                        raise RuntimeError("no art_prompt specified in sheet")
                    path = unused_path(cache_dir / f"{_artwork_stem(card)}.png")
                    generated = generator.generate(_generation_prompt(card, self.config.images.default_art_box_mm), path)
                    if isinstance(generated, Path):
                        path = generated
                    if not path.is_file():
                        raise RuntimeError("Artwork generation did not produce an image file.")
                except Exception as exc:
                    errors.append(f"{provider} {generator.model}, attempt {attempt}: {_artwork_error_message(exc)}")
                    self.progress(f"{label} - attempt failed: {errors[-1]}")
                    if not (card.art_prompt or "").strip() or (is_moderation_blocked(exc) and moderation_stage(exc) == "input"):
                        break
                else:
                    return self._finish_generated(card, path, credit, errors)
        try:
            # Leading / is a project-relative UI convention, never a filesystem root.
            folder = (self.project_root / self.config.images.default_art_folder.lstrip("/\\")).resolve()
            if not folder.is_relative_to(self.project_root) or folder.is_relative_to((self.project_root / self.config.paths.release_root).resolve()):
                raise ValueError("Default art folder must be inside the project and outside releases.")
            path = _existing_cached_artwork(folder, card)
            if path is None:
                raise FileNotFoundError(f"No art named {_artwork_stem(card)} in {self.config.images.default_art_folder}.")
            if path.suffix.lower() != ".svg":
                with Image.open(path) as image:
                    image.verify()
            return self._finish_generated(card, path, "OpenAI gpt-image-1", errors)
        except Exception as exc:
            errors.append(f"Default art folder: {_artwork_error_message(exc)}")
        message = "Artwork generation and fallback failed.\n" + "\n".join(errors)
        self.progress(f"{label} - creating error placeholder")
        # Do not write a permanent moderation marker: both providers must be retried next run.
        return self._prepare_placeholder(cache_dir, card, message), None, [
            f"Using placeholder artwork for {card.collector_number} - {card.name}: {message}"
        ]

    def _finish_generated(self, card: CardDefinition, path: Path, credit: str,
                          errors: list[str]) -> tuple[Path, GeneratedArtworkUpdate | None, list[str]]:
        card.artwork_url = _relative_to_project(path, self.project_root)
        card.artist = credit
        output = self._use_artwork(path, card)
        update = GeneratedArtworkUpdate(card.source_row_number, card.artwork_url, credit) if card.source_row_number else None
        warnings = [f"Artwork for {card.collector_number} - {card.name} succeeded after fallback or retry.\n" + "\n".join(errors)] if errors else []
        return output, update, warnings

    def _prepare_card(self, card: CardDefinition, generator: ImageGenerator, cache_dir: Path) -> tuple[Path, GeneratedArtworkUpdate | None]:
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
            generated = generator.generate(_generation_prompt(card, self.config.images.default_art_box_mm), cached_path)
            if isinstance(generated, Path):
                cached_path = generated
        else:
            self.progress(f"Using cached artwork: {card.name}")
        if not cached_path.is_file():
            raise RuntimeError("Artwork generation did not produce an image file.")
        relative_path = _relative_to_project(cached_path, self.project_root)
        output_path = self._use_artwork(cached_path, card)
        card.artwork_url = relative_path
        card.artist = f"Google {generator.model}" if isinstance(generator, GeminiImageGenerator) else self.config.images.generated_artist_credit
        update = GeneratedArtworkUpdate(card.source_row_number, relative_path, card.artist) if card.source_row_number else None
        return output_path, update

    def _prepare_placeholder(self, cache_dir: Path, card: CardDefinition, message: str) -> Path:
        # Fallbacks must never become successful cache hits or overwrite source fields in Sheets.
        cached_path = unused_path(cache_dir / "placeholders" / f"{_artwork_stem(card)}.png")
        cached_path = _write_placeholder_png(cached_path, card.name, message)
        card.artist = "Pipeline placeholder"
        return self._use_artwork(cached_path, card)

    def _next_shared_placeholder(self, card: CardDefinition, offsets: dict[str, int]) -> Path | None:
        placeholders_dir = self.project_root / self.config.paths.artwork_root / "placeholders"
        for subtype in card.subtypes:
            stem = slugify(subtype)
            candidates = _shared_placeholder_candidates(placeholders_dir, stem)
            offset = offsets.get(stem, 0)
            if offset < len(candidates):
                offsets[stem] = offset + 1
                return candidates[offset]
        return None

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
                cached_path = write_new_image(cached_path, source_path.read_bytes())
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
        metadata_path = cache_dir / f"{_artwork_stem(card)}.source.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            cached_path = (cache_dir / metadata["filename"]).resolve()
            if metadata["url"] == card.artwork_url and cached_path.is_relative_to(cache_dir.resolve()) and cached_path.is_file():
                return cached_path
        except (OSError, ValueError, KeyError, TypeError):
            pass
        base_path = cache_dir / f"{_artwork_stem(card)}{_image_suffix_from_url(card.artwork_url or '') or '.jpg'}"
        response = requests.get(card.artwork_url, timeout=self.timeout_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f"Artwork download failed for {card.collector_number} - {card.name}: HTTP {response.status_code}.")
        path = with_suffix_for_content_type(base_path, response.headers.get("content-type"))
        path = write_new_image(path, response.content)
        write_json(metadata_path, {"url": card.artwork_url, "filename": path.name})
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
        return safe_error(moderation_blocked_message(exc))
    return safe_error(f"{type(exc).__name__}: {exc}")


def _existing_cached_artwork(cache_dir: Path, card: CardDefinition) -> Path | None:
    stem = _artwork_stem(card)
    if cache_dir.is_dir():
        for path in sorted(cache_dir.iterdir(), key=lambda path: path.name.casefold()):
            if path.is_file() and slugify(path.stem) == stem and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
                return path
    return None


def _artwork_stem(card: CardDefinition) -> str:
    return slugify(card.name)


def _shared_placeholder_candidates(placeholders_dir: Path, card_stem: str) -> list[Path]:
    if not placeholders_dir.is_dir():
        return []
    matches: list[tuple[int, Path]] = []
    for path in placeholders_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        match = re.fullmatch(r"(.+?)(\d+)", path.stem)
        if match and slugify(match.group(1)) == card_stem:
            matches.append((int(match.group(2)), path))
    return [path for _, path in sorted(matches, key=lambda item: (item[0], item[1].name.casefold()))]


def _relative_to_project(path: Path, project_root: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _image_suffix_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg"} else ""


def _write_placeholder_png(path: Path, card_name: str, message: str) -> Path:
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
    buffer = BytesIO()
    image.save(buffer, format="PNG", pnginfo=metadata)
    return write_new_image(path, buffer.getvalue())
