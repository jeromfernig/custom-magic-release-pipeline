import json
from math import ceil
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from mtg_pipeline.canonicalize import canonicalize, write_canonical_json
from mtg_pipeline.config import AppConfig, PrintConfig
from mtg_pipeline.exporters.archetypes_pdf import _card_grid, export_archetypes_pdf
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.sources.google_sheets import _parse_table_values
from mtg_pipeline.steps import build_pipeline
from tests.fixtures import sample_source


@pytest.mark.parametrize("value, expected", [
    (None, []), ("", []), (" , , ", []), ("  A  ", ["A"]),
    (" A, B , Blue Tempo, ", ["A", "B", "Blue Tempo"]),
    ([" A ", "", " B"], ["A", "B"]),
])
def test_archetype_values_are_trimmed_lists(value, expected):
    card = CardDefinition(id="CW01", name="Card", set_code="PKM", rarity="common", archetype=value)
    assert card.archetype == expected


def test_sheet_archetypes_reach_canonical_json_and_support_custom_column(tmp_path):
    source = sample_source()
    row = source.card_rows[0].copy()
    row["Draft tags"] = "  A, B , Blue Tempo, , "
    source.card_rows = _parse_table_values([list(row), list(row.values())])
    config = AppConfig()
    config.columns.archetype = "Draft tags"
    set_def, cards = canonicalize(source, config)
    assert cards[0].archetype == ["A", "B", "Blue Tempo"]
    write_canonical_json(set_def, cards, tmp_path)
    raw = json.loads((tmp_path / "data/cards.json").read_text(encoding="utf-8"))
    assert raw[0]["archetype"] == ["A", "B", "Blue Tempo"]
    assert CardDefinition.model_validate(raw[0]).archetype == ["A", "B", "Blue Tempo"]
    _, older_cards = canonicalize(sample_source(), AppConfig())
    assert all(card.archetype == [] for card in older_cards)


def test_pdf_has_one_page_per_archetype_and_repeats_only_across_pages(tmp_path):
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="1.0")
    cards = [
        CardDefinition(id="CW01", name="First", set_code="TST", rarity="common", archetype="A, B, A", rendered_image_filename="first.jpg"),
        CardDefinition(id="CU01", name="Untagged", set_code="TST", rarity="common", archetype="", rendered_image_filename="untagged.jpg"),
        CardDefinition(id="UW01", name="Second", set_code="TST", rarity="uncommon", archetype="A", rendered_image_filename="second.jpg"),
        CardDefinition(id="UC01", name="Missing", set_code="TST", rarity="uncommon", archetype="C", rendered_image_filename="missing.jpg"),
    ]
    (tmp_path / "cards").mkdir()
    for card in cards[:3]:
        Image.new("RGB", (63, 88), "green").save(tmp_path / "cards" / card.expected_rendered_filename)
    output = tmp_path / "archetypes.pdf"
    real = Canvas(str(output), pagesize=landscape(A4))
    canvas = Mock(wraps=real)
    pages = [[]]
    def draw_image(path, *args, **kwargs):
        pages[-1].append(path)
        real.drawImage(path, *args, **kwargs)
    def new_page():
        pages.append([])
        real.showPage()
    canvas.drawImage.side_effect = draw_image
    canvas.showPage.side_effect = new_page
    with patch("reportlab.pdfgen.canvas.Canvas", return_value=canvas) as constructor:
        assert export_archetypes_pdf(set_def, list(reversed(cards)), tmp_path,
                                     PrintConfig(copies_by_rarity={"common": 4}), output) == output
    constructor.assert_called_once_with(str(output), pagesize=landscape(A4))
    assert [[name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for name in page] for page in pages] == [
        ["first.jpg", "second.jpg"], ["first.jpg"], [],
    ]
    assert canvas.showPage.call_count == 2
    labels = [call.args[2] for call in canvas.drawString.call_args_list]
    assert [label for label in labels if label in {"A", "B", "C"}] == ["A", "B", "C"]
    assert "No rendered cards are available for this archetype." in labels
    assert b"/Count 3" in output.read_bytes()


@pytest.mark.parametrize("count", [1, 2, 6, 12, 30, 178, 1000])
def test_grid_fits_all_cards_without_stretching_or_crossing_margins(count):
    width, height = 277 * mm, 176 * mm
    columns, card_width, card_height, gap = _card_grid(count, width, height, 63 / 88, 4 * mm)
    rows = ceil(count / columns)
    assert card_width > 0 and card_height > 0 and gap > 0
    assert columns * rows >= count
    assert columns * card_width + (columns - 1) * gap <= width + 1e-6
    assert rows * card_height + (rows - 1) * gap <= height + 1e-6
    assert card_width / card_height == pytest.approx(63 / 88)
    if count >= 6:
        assert card_height < _card_grid(1, width, height, 63 / 88, 4 * mm)[2]


def test_no_archetypes_produces_no_empty_pdf(tmp_path):
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="1.0")
    output = tmp_path / "archetypes.pdf"
    assert export_archetypes_pdf(set_def, [], tmp_path, PrintConfig(), output) is None
    assert not output.exists()


def test_archetypes_step_tracks_output_and_requires_rendering(tmp_path):
    runner = build_pipeline()
    step = runner.by_id["archetypes"]
    assert step.dependencies == ("rendering",)
    assert not runner.availability({"source", "validation", "canonicalization", "archetypes"})["archetypes"][0]
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="1.0")
    context = PipelineContext(config=AppConfig(), set_definition=set_def, release_dir=tmp_path)
    output = tmp_path / "archetypes/tst-1.0-archetypes.pdf"
    with patch("mtg_pipeline.steps.export_archetypes_pdf", return_value=output) as export:
        assert step.execute(context) == [output]
    export.assert_called_once_with(set_def, [], tmp_path, context.config.print, output)
    with patch("mtg_pipeline.steps.export_archetypes_pdf", return_value=None):
        assert step.execute(context) == []
