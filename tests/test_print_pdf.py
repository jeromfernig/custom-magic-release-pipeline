from mtg_pipeline.config import PrintConfig
from mtg_pipeline.exporters.print_pdf import _print_image_paths
from mtg_pipeline.models import CardDefinition


def test_pdf_uses_custom_id_rarity_color_and_number_order(tmp_path):
    expected_ids = [
        "CW2", "CW10", "CU01", "CB01", "CR01", "CG01", "CC01",
        "CA01", "CM01",  # Unlisted colors follow the specified colors.
        "UW01", "UU01", "RW01", "MW01", "TW01",
    ]
    cards = [
        CardDefinition(id=card_id, name=card_id, set_code="PKM", rarity="common",
                       collector_number=str(index), rendered_image_filename=f"{card_id}.jpg")
        for index, card_id in enumerate(reversed(expected_ids))
    ]
    (tmp_path / "cards").mkdir()
    for card in cards:
        (tmp_path / "cards" / card.expected_rendered_filename).touch()

    paths = _print_image_paths(cards, tmp_path, PrintConfig())

    assert [path.stem for path in paths] == expected_ids
