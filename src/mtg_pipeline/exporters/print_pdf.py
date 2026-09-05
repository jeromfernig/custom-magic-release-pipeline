from __future__ import annotations

from pathlib import Path

from mtg_pipeline.config import PrintConfig
from mtg_pipeline.models import CardDefinition, SetDefinition


def print_copy_count(card: CardDefinition, config: PrintConfig) -> int:
    return int(config.copies_by_rarity.get(card.rarity, 1))


def export_print_pdf(set_def: SetDefinition, cards: list[CardDefinition], release_dir: Path, config: PrintConfig, output_path: Path) -> Path:
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError as exc:
        raise RuntimeError("ReportLab is required for PDF export. Run: pip install -e .") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_size = landscape(A4) if config.orientation == "landscape" else A4
    width, height = page_size
    card_w = config.card_width_mm * mm
    card_h = config.card_height_mm * mm
    gap_x = (width - 4 * card_w) / 5
    gap_y = (height - 2 * card_h) / 3
    c = canvas.Canvas(str(output_path), pagesize=page_size)
    image_paths = _print_image_paths(cards, release_dir, config)
    for index, image_path in enumerate(image_paths):
        slot = index % 8
        if index and slot == 0:
            c.showPage()
        col = slot % 4
        row = slot // 4
        x = gap_x + col * (card_w + gap_x)
        y = height - gap_y - (row + 1) * card_h - row * gap_y
        c.drawImage(str(image_path), x, y, width=card_w, height=card_h, preserveAspectRatio=True, anchor="c")
        _crop_guides(c, x, y, card_w, card_h)
    c.save()
    return output_path


def _print_image_paths(cards: list[CardDefinition], release_dir: Path, config: PrintConfig) -> list[Path]:
    paths: list[Path] = []
    for card in sorted(cards, key=lambda c: c.collector_number):
        image_path = release_dir / "cards" / card.expected_rendered_filename
        if not image_path.exists():
            continue
        paths.extend([image_path] * print_copy_count(card, config))
    return paths


def _crop_guides(c: object, x: float, y: float, w: float, h: float) -> None:
    guide = 5
    c.setLineWidth(0.25)
    for gx, gy, dx, dy in (
        (x, y, -guide, 0),
        (x, y, 0, -guide),
        (x + w, y, guide, 0),
        (x + w, y, 0, -guide),
        (x, y + h, -guide, 0),
        (x, y + h, 0, guide),
        (x + w, y + h, guide, 0),
        (x + w, y + h, 0, guide),
    ):
        c.line(gx, gy, gx + dx, gy + dy)
