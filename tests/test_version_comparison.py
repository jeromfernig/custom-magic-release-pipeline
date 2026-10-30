import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from mtg_pipeline.compare_versions import SavedRelease, compare_cards, export_comparison_pdf, load_release, main
from mtg_pipeline.models import CardDefinition, SetDefinition


def release(tmp_path, version, cards):
    directory = tmp_path / version
    (directory / "cards").mkdir(parents=True)
    for card in cards:
        Image.new("RGB", (63, 88), "green").save(directory / "cards" / card.expected_rendered_filename)
    return SavedRelease(directory, SetDefinition(set_id="test", set_code="TST", set_name="Test", version=version), {card.id: card for card in cards})


def card(card_id, **kwargs):
    return CardDefinition(id=card_id, name=card_id, set_code="TST", rarity="common", **kwargs)


def test_comparison_pairs_by_id_and_distinguishes_added_removed_changed_unchanged(tmp_path):
    old_cards = [card("CW01", oracle_text="Before", archetype="A"), card("CW02"), card("CW03")]
    new_cards = [card("CW04"), card("CW02", render_cache_key="new-render", source_row_number=20),
                 card("CW01", oracle_text="After", archetype="A, B")]
    before, after = release(tmp_path, "0.1.0", old_cards), release(tmp_path, "0.2.0", new_cards)
    results = {result.id: result for result in compare_cards(before, after)}
    assert {key: value.status for key, value in results.items()} == {"CW01": "changed", "CW02": "unchanged", "CW03": "removed", "CW04": "added"}
    assert results["CW01"].changes == {"archetype": (["A"], ["A", "B"]), "oracle_text": ("Before", "After")}
    assert next(item for item in compare_cards(before, after, True) if item.id == "CW02").status == "changed"
    assert results["CW03"].new is None and results["CW04"].old is None


def test_artwork_hash_and_collector_number_changes_are_compared(tmp_path):
    before = release(tmp_path, "0.1.0", [card("CW01", artwork_sha256="before", collector_number="001")])
    after = release(tmp_path, "0.2.0", [card("CW01", artwork_sha256="after", collector_number="002")])
    assert set(compare_cards(before, after)[0].changes) == {"artwork_sha256", "collector_number"}


def test_old_canonical_json_defaults_and_duplicate_id_rejection(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "set.json").write_text(SetDefinition(set_id="test", set_name="Test", set_code="TST", version="0.1.0").model_dump_json())
    raw = card("CW01").model_dump(mode="json")
    del raw["archetype"]
    (data / "cards.json").write_text(json.dumps([raw]))
    assert load_release(tmp_path).cards["CW01"].archetype == []
    (data / "cards.json").write_text(json.dumps([raw, raw]))
    with pytest.raises(ValueError, match="Duplicate card ID"):
        load_release(tmp_path)


def test_pdf_contains_renderings_for_changes_but_not_unchanged_cards(tmp_path):
    from reportlab.platypus import Image as ReportImage
    before = release(tmp_path, "0.1.0", [card("CW01", oracle_text="Before"), card("CW02"), card("CW03")])
    after = release(tmp_path, "0.2.0", [card("CW01", oracle_text="After " + "long text " * 300), card("CW02"), card("CW04")])
    output = tmp_path / "comparison.pdf"
    with patch("reportlab.platypus.Image", wraps=ReportImage) as image:
        export_comparison_pdf(before, after, compare_cards(before, after), output, tmp_path)
    assert image.call_count == 4  # Two changed renderings, one removed, one added.
    assert not any("cw02" in str(call.args[0]).lower() for call in image.call_args_list)
    assert output.read_bytes().startswith(b"%PDF")


def test_missing_renderings_are_reported_without_network_or_generation(tmp_path):
    before = release(tmp_path, "0.1.0", [card("CW01")])
    after = release(tmp_path, "0.2.0", [card("CW01", oracle_text="Changed")])
    for saved in (before, after):
        for path in (saved.directory / "cards").iterdir():
            path.unlink()
    output = export_comparison_pdf(before, after, compare_cards(before, after), tmp_path / "missing.pdf", tmp_path)
    assert output.is_file()


def test_cli_reports_missing_version_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["compare_versions", "0.2.0", "0.3.0", "--release-root", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert "Version comparison failed" in capsys.readouterr().err


def test_cli_rejects_path_traversal(monkeypatch):
    monkeypatch.setattr("sys.argv", ["compare_versions", "../outside", "0.3.0"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
