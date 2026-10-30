from __future__ import annotations

from collections import defaultdict
from math import ceil
from pathlib import Path

from mtg_pipeline.config import PrintConfig
from mtg_pipeline.exporters.print_pdf import _print_order
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.linked_cards import display_cards


def _card_grid(count: int, width: float, height: float, aspect_ratio: float,
               preferred_gap: float) -> tuple[int, float, float, float]:
    """Choose the column count giving the largest undistorted card on one page."""
    best = None
    for columns in range(1, count + 1):
        rows = ceil(count / columns)
        gap = min(preferred_gap, width / (2 * columns), height / (2 * rows))
        card_height = min((height - (rows - 1) * gap) / rows,
                          (width - (columns - 1) * gap) / columns / aspect_ratio)
        candidate = (card_height, columns, gap)
        if best is None or card_height > best[0]:
            best = candidate
    card_height, columns, gap = best
    return columns, card_height * aspect_ratio, card_height, gap


def export_archetypes_pdf(set_def: SetDefinition, cards: list[CardDefinition], release_dir: Path,
                          config: PrintConfig, output_path: Path) -> Path | None:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    groups: dict[str, list[CardDefinition]] = defaultdict(list)
    for card in display_cards(cards):
        for archetype in dict.fromkeys(card.archetype):
            groups[archetype].append(card)
    if not groups:
        return None
    if config.card_width_mm <= 0 or config.card_height_mm <= 0:
        raise ValueError("Card dimensions must be positive for the archetypes PDF.")

    page_width, page_height = landscape(A4)
    margin = 10 * mm
    header_height = 14 * mm
    width = page_width - 2 * margin
    height = page_height - 2 * margin - header_height
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output_path), pagesize=(page_width, page_height))
    pdf.setTitle(f"{set_def.set_name} {set_def.version} - Archetypes")
    for page, archetype in enumerate(sorted(groups, key=lambda value: (value.casefold(), value))):
        if page:
            pdf.showPage()
        members = groups[archetype]
        rendered = [(card, release_dir / "cards" / card.expected_rendered_filename)
                    for card in members if (release_dir / "cards" / card.expected_rendered_filename).is_file()]
        title_size = min(16, width / max(1, stringWidth(archetype, "Helvetica-Bold", 1)))
        pdf.setFont("Helvetica-Bold", title_size)
        pdf.drawString(margin, page_height - margin - 5 * mm, archetype)
        pdf.setFont("Helvetica", 8)
        missing = len(members) - len(rendered)
        summary = f"{len(rendered)} rendered card(s)"
        if missing:
            summary += f"; {missing} card(s) without rendered images omitted"
        pdf.drawString(margin, page_height - margin - 10 * mm, summary)
        if not rendered:
            pdf.drawString(margin, page_height - margin - header_height - 8 * mm,
                           "No rendered cards are available for this archetype.")
            continue
        columns, card_width, card_height, gap = _card_grid(
            len(rendered), width, height, config.card_width_mm / config.card_height_mm, 4 * mm,
        )
        rows = ceil(len(rendered) / columns)
        left = margin + (width - columns * card_width - (columns - 1) * gap) / 2
        top = page_height - margin - header_height - (height - rows * card_height - (rows - 1) * gap) / 2
        for index, (_, image_path) in enumerate(rendered):
            row, column = divmod(index, columns)
            pdf.drawImage(str(image_path), left + column * (card_width + gap),
                          top - card_height - row * (card_height + gap),
                          width=card_width, height=card_height, preserveAspectRatio=True, anchor="c")
    pdf.save()
    return output_path
