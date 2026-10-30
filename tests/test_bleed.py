from unittest.mock import Mock, patch

import pytest
from PIL import Image, ImageDraw
from reportlab.lib.units import mm
from streamlit.testing.v1 import AppTest

from mtg_pipeline.config import PrintConfig
from mtg_pipeline.exporters.bleed import matte_card_corners
from mtg_pipeline.exporters.print_pdf import export_print_pdf
from mtg_pipeline.models import CardDefinition, SetDefinition
from tests.test_ui import APP_PATH, source_mocks
from tests.fixtures import sample_source


def rendering():
    image = Image.new("RGB", (630, 880), "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 629, 879), radius=40, fill=(4, 5, 6))
    draw.rectangle((40, 80, 590, 780), fill="white")
    draw.rectangle((80, 140, 500, 350), fill="green")
    return image


def test_corner_matte_matches_sampled_border_preserves_card_interior_and_source():
    source = rendering()
    original = source.tobytes()
    matted, black = matte_card_corners(source)
    assert black == (4, 5, 6)
    assert source.tobytes() == original
    assert matted.size == source.size
    for point in ((0, 0), (629, 0), (0, 879), (629, 879)):
        assert matted.getpixel(point) == black
    assert matted.crop((40, 80, 590, 780)).tobytes() == source.crop((40, 80, 590, 780)).tobytes()


def setup(tmp_path):
    card = CardDefinition(id="CW01", name="Test", set_code="TST", rarity="common", collector_number="001")
    set_def = SetDefinition(set_id="test", set_name="Test", set_code="TST", version="0.3.0")
    path = tmp_path / "cards" / card.expected_rendered_filename
    path.parent.mkdir()
    rendering().save(path)
    return card, set_def, path


def test_pdf_bleed_extends_two_mm_without_scaling_card_and_marks_stay_at_trim(tmp_path):
    card, set_def, path = setup(tmp_path)
    original = path.read_bytes()
    canvas = Mock()
    with patch("reportlab.pdfgen.canvas.Canvas", return_value=canvas):
        export_print_pdf(set_def, [card], tmp_path, PrintConfig(), tmp_path / "print.pdf")
    image_call = canvas.drawImage.call_args
    _, x, y = image_call.args
    assert image_call.kwargs["width"] == pytest.approx(63 * mm)
    assert image_call.kwargs["height"] == pytest.approx(88 * mm)
    rect = canvas.rect.call_args
    assert rect.args == pytest.approx((x - 2 * mm, y - 2 * mm, 67 * mm, 92 * mm))
    assert canvas.setFillColorRGB.call_args.args == pytest.approx((4 / 255, 5 / 255, 6 / 255), abs=.005)
    left_mark = canvas.line.call_args_list[0].args
    assert left_mark[1] == y and left_mark[0] < x - 2 * mm
    assert path.read_bytes() == original


def test_disabling_bleed_uses_original_image_and_no_rectangle(tmp_path):
    card, set_def, path = setup(tmp_path)
    canvas = Mock()
    with patch("reportlab.pdfgen.canvas.Canvas", return_value=canvas), patch("mtg_pipeline.exporters.bleed.matte_card_corners") as matte:
        export_print_pdf(set_def, [card], tmp_path, PrintConfig(bleed_enabled=False), tmp_path / "print.pdf")
    assert canvas.drawImage.call_args.args[0] == str(path)
    canvas.rect.assert_not_called()
    matte.assert_not_called()


def test_bleed_must_fit_sheet(tmp_path):
    card, set_def, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="bleed overlap"):
        export_print_pdf(set_def, [card], tmp_path, PrintConfig(bleed_mm=10), tmp_path / "bad.pdf")


def test_ui_bleed_is_on_by_default_and_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with source_mocks(sample_source()), patch("mtg_pipeline.artwork.r2.r2_settings", return_value=("", "", False)):
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        checkbox = next(field for field in app.checkbox if field.label == "Add 2 mm black bleed")
        assert checkbox.value
        checkbox.uncheck().run()
        assert not next(field for field in app.checkbox if field.label == "Add 2 mm black bleed").value
        assert not app.exception
