from __future__ import annotations

from pathlib import Path

from mtg_pipeline.config import PrintConfig
from mtg_pipeline.exporters.print_pdf import _print_order
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.linked_cards import display_cards


def export_notes_pdf(set_def: SetDefinition, cards: list[CardDefinition], release_dir: Path,
                     config: PrintConfig, output_path: Path) -> Path:
    """One copy per rendered card, in six rows of two card-and-feedback pairs."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen import canvas

    if config.card_width_mm <= 0 or config.card_height_mm <= 0:
        raise ValueError("Card dimensions must be positive for the note PDF.")
    available = [(card, release_dir / "cards" / card.expected_rendered_filename)
                 for card in display_cards(cards)
                 if (release_dir / "cards" / card.expected_rendered_filename).is_file()]
    if not available:
        raise ValueError("No rendered card images are available for the note PDF.")

    page_width, page_height = A4
    margin = 10 * mm
    row_gap = 3 * mm
    column_gap = 8 * mm
    writing_gap = 4 * mm
    rows = 6
    card_height = (page_height - 2 * margin - (rows - 1) * row_gap) / rows
    card_width = card_height * config.card_width_mm / config.card_height_mm
    column_width = (page_width - 2 * margin - column_gap) / 2
    writing_width = column_width - card_width - writing_gap
    if writing_width < 30 * mm:
        raise ValueError("Card aspect ratio leaves too little feedback space in the note PDF.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(output_path), pagesize=(page_width, page_height))
    pdf.setTitle(f"{set_def.set_name} {set_def.version} - Feedback notes")
    for index, (card, image_path) in enumerate(available):
        slot = index % 12
        if index and slot == 0:
            pdf.showPage()
        row, column = divmod(slot, 2)
        x = margin + column * (column_width + column_gap)
        top = page_height - margin - row * (card_height + row_gap)
        pdf.drawImage(str(image_path), x, top - card_height, width=card_width, height=card_height,
                      preserveAspectRatio=True, anchor="c")
        text_x = x + card_width + writing_gap
        pdf.setFont("Helvetica", 7)
        label = f"{card.id} - {card.name}"
        label_lines = simpleSplit(label, "Helvetica", 7, writing_width)
        pdf.drawString(text_x, top - 3 * mm, label_lines[0])
        pdf.setStrokeColorRGB(0.65, 0.65, 0.65)
        pdf.setLineWidth(0.35)
        for line in range(4):
            y = top - 9 * mm - line * (card_height - 14 * mm) / 3
            pdf.line(text_x, y, x + column_width, y)
    pdf.save()
    return output_path
