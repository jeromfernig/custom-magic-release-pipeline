"""Prune obsolete release renderings against the current canonical card list."""
from pathlib import Path

from mtg_pipeline.linked_cards import image_cards


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".avif"}


def prune_card_images(cards, release_dir: Path, project_root: Path) -> list[Path]:
    root = project_root.resolve()
    requested = root / release_dir
    release = requested.resolve()
    if release == root or not release.is_relative_to(root):
        raise ValueError("Release cleanup must stay inside the project release directory.")
    if release != requested.absolute():
        raise ValueError("Refusing to clean a redirected release directory.")
    directory = release / "cards"
    if directory.is_symlink() or getattr(directory, "is_junction", lambda: False)():
        raise ValueError("Refusing to clean a redirected release cards directory.")
    if directory.resolve() != directory:
        raise ValueError("Release cards cleanup resolved outside its expected directory.")
    expected = set()
    for card in image_cards(cards):
        name = card.expected_rendered_filename
        if not name or Path(name).name != name or "/" in name or "\\" in name or ":" in name or name in {".", ".."}:
            raise ValueError(f"Invalid release card image filename: {name!r}")
        expected.add(name.casefold())
    if not directory.exists():
        return []
    removed = []
    # Nonrecursive: never visit cache, artwork, other releases, or subfolders.
    for path in sorted(directory.iterdir()):
        if path.suffix.casefold() not in IMAGE_SUFFIXES or path.name.casefold() in expected:
            continue
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed.append(path)
    return removed
