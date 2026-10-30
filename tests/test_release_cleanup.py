from pathlib import Path

import pytest

from mtg_pipeline.canonicalize import canonicalize
from mtg_pipeline.config import AppConfig
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.release_cleanup import prune_card_images
from mtg_pipeline.steps import build_pipeline
from tests.fixtures import sample_source
from tests.test_linked_cards import source as linked_source


def test_cleanup_removes_renamed_removed_and_legacy_images_only_in_current_release(tmp_path):
    config = AppConfig()
    _, cards = canonicalize(sample_source(), config)
    release = Path("releases/pokemon-mtg-set/0.3.0")
    directory = tmp_path / release / "cards"
    directory.mkdir(parents=True)
    current = directory / cards[0].expected_rendered_filename
    current.write_bytes(b"current rendering")
    stale = [directory / name for name in ("001-old-name.jpg", "removed-face-back.jpg", "legacy.png")]
    for path in stale:
        path.write_bytes(b"obsolete")
    preserved = [directory / "README.txt", directory / "nested/archive.jpg",
                 tmp_path / "releases/pokemon-mtg-set/0.2.0/cards/old.jpg",
                 tmp_path / "artwork/original.png", tmp_path / ".cache/mtg_crucible/renders/hash.jpg"]
    for path in preserved:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"preserve")
    assert set(prune_card_images(cards, release, tmp_path)) == set(stale)
    assert current.read_bytes() == b"current rendering"
    assert all(path.read_bytes() == b"preserve" for path in preserved)
    assert prune_card_images(cards, release, tmp_path) == []


def test_canonicalization_cleans_even_without_rendering_and_ignores_render_limit(tmp_path):
    config = AppConfig()
    config.crucible.render_limit = 1
    source = sample_source()
    _, cards = canonicalize(source, config)
    release = tmp_path / "releases/pokemon-mtg-set/0.3.0"
    directory = release / "cards"
    directory.mkdir(parents=True)
    for card in cards:
        (directory / card.expected_rendered_filename).write_bytes(b"existing")
    (directory / "stale.jpg").write_bytes(b"old")
    context = PipelineContext(config=config, project_root=tmp_path, release_dir=release, source_data=source)
    outputs = build_pipeline(config).by_id["canonicalization"].execute(context)
    assert {path.name for path in directory.iterdir()} == {card.expected_rendered_filename for card in cards}
    assert context.statistics["release_cleanup"] == {"removed_card_images": 1, "removed_filenames": ["stale.jpg"]}
    assert all(path.is_file() for path in outputs)


def test_changing_and_removing_link_prunes_obsolete_faces(tmp_path):
    config = AppConfig()
    data = linked_source("transform")
    _, cards = canonicalize(data, config)
    directory = tmp_path / "release/cards"
    directory.mkdir(parents=True)
    for card in cards:
        (directory / card.expected_rendered_filename).write_bytes(b"render")
    back = directory / cards[1].expected_rendered_filename
    assert prune_card_images(cards, Path("release"), tmp_path) == []
    data.card_rows[0]["link_type"] = "prepare"
    _, prepared = canonicalize(data, config)
    assert prune_card_images(prepared, Path("release"), tmp_path) == [back]
    assert len(list(directory.iterdir())) == 1
    data.card_rows[0].update(link_id="", link_type="")
    data.card_rows[1]["include"] = "disabled"
    _, ordinary = canonicalize(data, config)
    assert prune_card_images(ordinary, Path("release"), tmp_path) == []


def test_missing_folder_and_empty_current_card_list(tmp_path):
    assert prune_card_images([], Path("release"), tmp_path) == []
    directory = tmp_path / "release/cards"
    directory.mkdir(parents=True)
    stale = directory / "removed.jpg"
    stale.touch()
    assert prune_card_images([], Path("release"), tmp_path) == [stale]


def test_unsafe_paths_are_rejected_before_removing_files(tmp_path):
    _, cards = canonicalize(sample_source(), AppConfig())
    directory = tmp_path / "release/cards"
    directory.mkdir(parents=True)
    stale = directory / "stale.jpg"
    stale.touch()
    cards[0].rendered_image_filename = "../escape.jpg"
    with pytest.raises(ValueError, match="filename"):
        prune_card_images(cards, Path("release"), tmp_path)
    assert stale.exists()
    with pytest.raises(ValueError, match="inside the project"):
        prune_card_images([], tmp_path.parent / "outside", tmp_path)


def test_redirected_cards_directory_is_not_cleaned(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    stale = external / "stale.jpg"
    stale.touch()
    release = tmp_path / "release"
    release.mkdir()
    try:
        (release / "cards").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symbolic links is unavailable for this account.")
    with pytest.raises(ValueError, match="redirected"):
        prune_card_images([], release, tmp_path)
    assert stale.exists()
