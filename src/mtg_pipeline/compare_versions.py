"""Compare two saved canonical releases by card ID, without running the pipeline."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from mtg_pipeline.exporters.print_pdf import _print_order
from mtg_pipeline.models import CardDefinition, SetDefinition


BUILD_FIELDS = {
    "source_row_number", "rendered_image_filename", "artwork_filename", "artwork_path",
    "artwork_public_url", "artwork_object_key", "rendered_public_url", "rendered_image_path", "render_cache_key",
}


@dataclass
class SavedRelease:
    directory: Path
    set_definition: SetDefinition
    cards: dict[str, CardDefinition]


@dataclass
class CardComparison:
    id: str
    status: str
    old: CardDefinition | None
    new: CardDefinition | None
    changes: dict[str, tuple[object, object]]


def load_release(directory: Path) -> SavedRelease:
    set_def = SetDefinition.model_validate_json((directory / "data/set.json").read_text(encoding="utf-8-sig"))
    raw = json.loads((directory / "data/cards.json").read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list):
        raise ValueError(f"Expected a canonical card list in {directory / 'data/cards.json'}")
    cards = {}
    for item in raw:
        card = CardDefinition.model_validate(item)
        if card.id in cards:
            raise ValueError(f"Duplicate card ID {card.id!r} in {directory}")
        cards[card.id] = card
    return SavedRelease(directory.resolve(), set_def, cards)


def compare_cards(old: SavedRelease, new: SavedRelease, include_build_fields: bool = False) -> list[CardComparison]:
    if old.set_definition.set_id != new.set_definition.set_id:
        raise ValueError("The releases must belong to the same set.")
    comparisons = []
    for card_id in sorted(old.cards.keys() | new.cards.keys(), key=lambda key: _print_order(new.cards.get(key) or old.cards[key])):
        before, after = old.cards.get(card_id), new.cards.get(card_id)
        old_data = before.model_dump(mode="json") if before else {}
        new_data = after.model_dump(mode="json") if after else {}
        fields = old_data.keys() | new_data.keys()
        if not include_build_fields:
            fields -= BUILD_FIELDS
        changes = {field: (old_data.get(field), new_data.get(field)) for field in sorted(fields)
                   if old_data.get(field) != new_data.get(field)}
        status = "added" if before is None else "removed" if after is None else "changed" if changes else "unchanged"
        comparisons.append(CardComparison(card_id, status, before, after, changes))
    return comparisons


def export_comparison_pdf(old: SavedRelease, new: SavedRelease, comparisons: list[CardComparison],
                           output: Path, project_root: Path, include_build_fields: bool = False,
                           progress: Callable[[str], None] | None = None) -> Path:
    from PIL import Image as PILImage
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="DiffCell", fontName="Helvetica", fontSize=8, leading=10, splitLongWords=True))
    width = landscape(A4)[0] - 24 * mm
    def paragraph(value, style="DiffCell"):
        return Paragraph(escape(str(value)).replace("\n", "<br/>"), styles[style])
    def formatted(value):
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    def diff_table(changes):
        rows = [[paragraph("Field"), paragraph("Old"), paragraph("New")]]
        rows.extend([paragraph(field), paragraph(formatted(before)), paragraph(formatted(after))]
                    for field, (before, after) in changes.items())
        table = Table(rows, colWidths=[38 * mm, (width - 38 * mm) / 2, (width - 38 * mm) / 2],
                      repeatRows=1, splitByRow=1, splitInRow=1)
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7edf4")),
            ("GRID", (0, 0), (-1, -1), .3, colors.lightgrey), ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        return table
    def rendering(release, card):
        if card is None:
            return paragraph("Not present in this version.")
        image_dir = (release.directory / "cards").resolve()
        path = (image_dir / card.expected_rendered_filename).resolve()
        candidates = [(path, image_dir, "Saved release rendering")]
        if card.rendered_image_path:
            candidates.append(((project_root / card.rendered_image_path).resolve(), project_root.resolve(), "Cached rendering (release image unavailable)"))
        for path, allowed, label in candidates:
            if not path.is_relative_to(allowed) or not path.is_file():
                continue
            try:
                with PILImage.open(path) as source:
                    source.load()
                    if "A" in source.getbands():
                        thumbnail = PILImage.new("RGB", source.size, "white")
                        thumbnail.paste(source, mask=source.getchannel("A"))
                    else:
                        thumbnail = source.convert("RGB")
                    thumbnail.thumbnail((1200, 1200), PILImage.Resampling.LANCZOS)
                    w, h = thumbnail.size
                    data = BytesIO()
                    thumbnail.save(data, format="JPEG", quality=90)
                    data.seek(0)
                scale = min(100 * mm / w, 104 * mm / h)
                return [Image(data, width=w * scale, height=h * scale), paragraph(label)]
            except (OSError, ValueError):
                continue
        return paragraph("Rendering unavailable locally. No rendering or downloading was attempted.")

    counts = Counter(item.status for item in comparisons)
    story = [paragraph(f"{new.set_definition.set_name}: {old.set_definition.version} vs {new.set_definition.version}", "Title"),
             paragraph("Card comparison by ID", "Heading2"),
             paragraph(" | ".join(f"{status.title()}: {counts[status]}" for status in ("changed", "added", "removed", "unchanged"))),
             Spacer(1, 5 * mm)]
    if not include_build_fields:
        story.append(paragraph("Comparison includes canonical card content, collector numbers, artwork references and artwork hashes. Build/cache paths, upload URLs, generated filenames and Sheet row numbers are ignored. Use --include-build-fields to compare those too."))
    old_set = old.set_definition.model_dump(mode="json")
    new_set = new.set_definition.model_dump(mode="json")
    set_changes = {field: (old_set.get(field), new_set.get(field)) for field in sorted(old_set.keys() | new_set.keys())
                   if field != "version" and old_set.get(field) != new_set.get(field)}
    if set_changes:
        story.extend([paragraph("Set metadata changes", "Heading2"), diff_table(set_changes)])
    for index, item in enumerate(comparisons, 1):
        if progress and (index == 1 or index % 25 == 0):
            progress(f"Preparing comparison {index}/{len(comparisons)}")
        if item.status == "unchanged":
            continue
        story.append(PageBreak())
        name = (item.new or item.old).name
        image_table = Table([
            [paragraph(f"Old: {old.set_definition.version}", "Heading3"), paragraph(f"New: {new.set_definition.version}", "Heading3")],
            [rendering(old, item.old), rendering(new, item.new)],
        ], colWidths=[width / 2] * 2)
        image_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
        story.extend([KeepTogether([paragraph(f"{item.id} — {item.status.title()} — {name}", "Heading2"), image_table]),
                      Spacer(1, 3 * mm), paragraph("Canonical field changes", "Heading3"), diff_table(item.changes)])
    unchanged = [item for item in comparisons if item.status == "unchanged"]
    if unchanged:
        story.extend([PageBreak(), paragraph("Unchanged cards", "Heading1"),
                      paragraph("These IDs have no differences in the compared canonical fields. Renderings are omitted."), Spacer(1, 4 * mm)])
        table = Table([[paragraph(item.id), paragraph(item.new.name)] for item in unchanged], colWidths=[35 * mm, width - 35 * mm])
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), .2, colors.lightgrey)]))
        story.append(table)
    output.parent.mkdir(parents=True, exist_ok=True)
    def footer(pdf, doc):
        if progress and (doc.page == 1 or doc.page % 25 == 0):
            progress(f"Writing PDF page {doc.page}")
        pdf.saveState()
        pdf.setFont("Helvetica", 8)
        pdf.drawRightString(landscape(A4)[0] - 12 * mm, 6 * mm, f"Page {doc.page}")
        pdf.restoreState()
    SimpleDocTemplate(str(output), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                      topMargin=10 * mm, bottomMargin=12 * mm,
                      title=f"{old.set_definition.version} vs {new.set_definition.version}").build(story, onFirstPage=footer, onLaterPages=footer)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_version")
    parser.add_argument("new_version")
    parser.add_argument("--set-id", default="pokemon-mtg-set")
    parser.add_argument("--release-root", type=Path, default=Path("releases"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Root for optional cached rendering lookup.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--include-build-fields", action="store_true")
    args = parser.parse_args()
    for value in (args.set_id, args.old_version, args.new_version):
        if not value or value in {".", ".."} or "/" in value or "\\" in value or ":" in value:
            parser.error("Set ID and versions must be single directory names.")
    base = args.release_root / args.set_id
    try:
        old, new = load_release(base / args.old_version), load_release(base / args.new_version)
        comparisons = compare_cards(old, new, args.include_build_fields)
        output = args.output or base / "comparisons" / f"{args.old_version}-vs-{args.new_version}.pdf"
        export_comparison_pdf(old, new, comparisons, output, args.project_root, args.include_build_fields,
                              progress=lambda message: print(message, flush=True))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Version comparison failed: {exc}\n")
    print(f"Saved: {output}")
    print(dict(Counter(item.status for item in comparisons)))


if __name__ == "__main__":
    main()
