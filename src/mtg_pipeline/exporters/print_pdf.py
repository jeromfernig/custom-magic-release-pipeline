from __future__ import annotations

from pathlib import Path
from io import BytesIO

from mtg_pipeline.config import PrintConfig
from mtg_pipeline.models import CardDefinition, SetDefinition


def print_copy_count(card: CardDefinition, config: PrintConfig) -> int:
    return int(config.copies_by_rarity.get(card.rarity, 1))


def _print_order(card: CardDefinition) -> tuple[int, int, str, int, int, str]:
    card_id = card.id.strip().upper()
    rarity = card_id[:1]
    color = card_id[1:2]
    number = card_id[2:]
    rarity_order = {value: index for index, value in enumerate("CURMT")}
    color_order = {value: index for index, value in enumerate("WUBRGC")}
    return (
        rarity_order.get(rarity, len(rarity_order)),
        color_order.get(color, len(color_order)),
        color,
        0 if number.isdecimal() else 1,
        int(number) if number.isdecimal() else 0,
        card_id,
    )


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
    bleed = config.bleed_mm * mm if config.bleed_enabled else 0
    if bleed and (gap_x < 2 * bleed or gap_y < 2 * bleed):
        raise ValueError("The configured card size and bleed overlap on the 4-by-2 print sheet. Reduce bleed or card dimensions.")
    c = canvas.Canvas(str(output_path), pagesize=page_size)
    image_paths = _print_image_paths(cards, release_dir, config)
    previews = {}
    for index, image_path in enumerate(image_paths):
        slot = index % 8
        if index and slot == 0:
            c.showPage()
        col = slot % 4
        row = slot // 4
        x = gap_x + col * (card_w + gap_x)
        y = height - gap_y - (row + 1) * card_h - row * gap_y
        image = str(image_path)
        if bleed:
            if image_path not in previews:
                from PIL import Image
                from reportlab.lib.utils import ImageReader
                from mtg_pipeline.exporters.bleed import matte_card_corners
                with Image.open(image_path) as source:
                    source.load()
                    matted, black = matte_card_corners(source)
                preview = BytesIO()
                matted.save(preview, format="JPEG", quality=95, subsampling=0)
                # Keep compressed previews rather than full decoded images for
                # every card in a set. ReportLab embeds the JPEG directly.
                previews[image_path] = (preview.getvalue(), black)
            preview, black = previews[image_path]
            from reportlab.lib.utils import ImageReader
            image = ImageReader(BytesIO(preview))
            c.saveState()
            c.setFillColorRGB(*(channel / 255 for channel in black))
            c.rect(x - bleed, y - bleed, card_w + 2 * bleed, card_h + 2 * bleed, stroke=0, fill=1)
            c.restoreState()
        c.drawImage(image, x, y, width=card_w, height=card_h, preserveAspectRatio=True, anchor="c")
        _crop_guides(c, x, y, card_w, card_h, bleed)
    c.save()
    return output_path


def _print_image_paths(cards: list[CardDefinition], release_dir: Path, config: PrintConfig) -> list[Path]:
    from mtg_pipeline.linked_cards import display_cards, physical_cards
    owners = {card.link_id: card for card in physical_cards(cards) if card.link_id}
    paths: list[Path] = []
    for card in display_cards(cards):
        image_path = release_dir / "cards" / card.expected_rendered_filename
        if not image_path.exists():
            continue
        paths.extend([image_path] * print_copy_count(owners.get(card.id, card), config))
    return paths


def _crop_guides(c: object, x: float, y: float, w: float, h: float, bleed: float = 0) -> None:
    guide = 5
    c.setLineWidth(0.25)
    offset = bleed + 1 if bleed else 0
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
        # Marks remain aligned to trim, but begin beyond the printed bleed.
        sx = gx + (offset if dx > 0 else -offset if dx < 0 else 0)
        sy = gy + (offset if dy > 0 else -offset if dy < 0 else 0)
        c.line(sx, sy, sx + dx, sy + dy)
