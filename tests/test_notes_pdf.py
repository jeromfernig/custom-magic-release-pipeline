from unittest.mock import Mock, patch

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from mtg_pipeline.config import PrintConfig
from mtg_pipeline.exporters.notes_pdf import export_notes_pdf
from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.steps import build_pipeline


def test_notes_pdf_layout_pagination_and_single_copies(tmp_path):
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="1.0")
    cards = [CardDefinition(id=f"CW{number:02}", name=f"Card {number}", set_code="TST", rarity="common",
                            rendered_image_filename=f"{number}.jpg") for number in range(13, 0, -1)]
    (tmp_path / "cards").mkdir()
    for card in cards:
        Image.new("RGB", (63, 88), "green").save(tmp_path / "cards" / card.expected_rendered_filename)
    config = PrintConfig(orientation="landscape", copies_by_rarity={"common": 4})
    output = tmp_path / "notes.pdf"
    canvas = Mock(wraps=Canvas(str(output), pagesize=A4))

    with patch("reportlab.pdfgen.canvas.Canvas", return_value=canvas) as factory:
        export_notes_pdf(set_def, cards, tmp_path, config, output)

    factory.assert_called_once_with(str(output), pagesize=A4)
    assert canvas.drawImage.call_count == 13
    assert canvas.line.call_count == 13 * 4
    assert canvas.showPage.call_count == 1
    calls = canvas.drawImage.call_args_list
    assert [call.args[0] for call in calls] == [str(tmp_path / "cards" / f"{number}.jpg") for number in range(1, 14)]
    width, height = A4
    expected_height = (297 - 20 - 15) / 6
    assert calls[0].kwargs["height"] / mm == pytest.approx(expected_height)
    assert calls[0].kwargs["width"] / mm == pytest.approx(expected_height * 63 / 88)
    for call in calls:
        _, x, y = call.args
        assert 10 * mm <= x < x + call.kwargs["width"] <= width - 10 * mm
        assert y >= 10 * mm - 1e-6
        assert y + call.kwargs["height"] <= height - 10 * mm + 1e-6
    assert len({round(call.args[2], 5) for call in calls[:12]}) == 6
    assert calls[0].args[1] < calls[1].args[1]
    assert calls[0].args[2] == calls[1].args[2]
    assert calls[2].args[2] < calls[0].args[2]
    for row in range(6):
        for col in range(2):
            call = calls[2 * row + col]
            for line in canvas.line.call_args_list[(2 * row + col) * 4:(2 * row + col + 1) * 4]:
                left, y, right, _ = line.args
                assert left > call.args[1] + call.kwargs["width"]
                assert right <= width - 10 * mm + 1e-6
                assert call.args[2] < y < call.args[2] + call.kwargs["height"]
    assert output.read_bytes().startswith(b"%PDF")
    assert b"/Count 2" in output.read_bytes()


def test_notes_step_uses_release_output_and_requires_rendering(tmp_path):
    runner = build_pipeline()
    step = runner.by_id["notes"]
    assert step.display_name == "Create Note File"
    assert not runner.availability({"source", "validation", "canonicalization", "notes"})["notes"][0]
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="1.0")
    from mtg_pipeline.config import AppConfig
    context = PipelineContext(config=AppConfig(), set_definition=set_def, release_dir=tmp_path)
    output = tmp_path / "notes/tst-1.0-notes.pdf"
    with patch("mtg_pipeline.steps.export_notes_pdf", return_value=output) as export:
        assert step.execute(context) == [output]
    export.assert_called_once_with(set_def, [], tmp_path, context.config.print, output)


def test_notes_pdf_rejects_empty_image_list(tmp_path):
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="1.0")
    with pytest.raises(ValueError, match="No rendered card images"):
        export_notes_pdf(set_def, [], tmp_path, PrintConfig(), tmp_path / "notes.pdf")
