import base64
import json
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

from mtg_pipeline.canonicalize import canonicalize
from mtg_pipeline.config import AppConfig, load_config
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.renderers.crucible import (
    CrucibleRendererClient, build_crucible_card, cache_root, render_crucible_cards,
)
from mtg_pipeline.steps import build_pipeline
from tests.fixtures import sample_source
from tests.test_ui import APP_PATH, source_mocks


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (100, 140), "green").save(buffer, format="JPEG")
    return buffer.getvalue()


def context(tmp_path):
    config = AppConfig()
    set_def, cards = canonicalize(sample_source(), config)
    card = next(card for card in cards if card.name == "Verdant Seedling")
    art = tmp_path / "artwork/manual.png"
    art.parent.mkdir()
    Image.new("RGB", (200, 140), "green").save(art)
    card.artwork_path = "artwork/manual.png"
    return PipelineContext(config=config, set_definition=set_def, cards=[card], project_root=tmp_path,
                           release_dir=Path("releases/0.1.0"))


def test_defaults_and_routing_require_no_source_art_upload(tmp_path):
    config = AppConfig()
    assert config.rendering.engine == "crucible"
    example = load_config(Path(__file__).resolve().parents[1] / "config.example.yaml")
    assert example.rendering.engine == "crucible"
    runner = build_pipeline(config)
    selected = {step.id for step in runner.steps} - {"artwork_r2_upload"}
    assert runner.availability(selected)["rendering"][0]
    assert runner.availability(selected)["print"][0]
    ctx = context(tmp_path)
    with patch("mtg_pipeline.steps.render_crucible_cards", return_value=[]) as local, patch("mtg_pipeline.steps.render_design_cards", return_value=[]) as remote:
        runner.by_id["rendering"].execute(ctx)
        local.assert_called_once_with(ctx)
        remote.assert_not_called()
        ctx.config.rendering.engine = "mtg_design"
        build_pipeline(ctx.config).by_id["rendering"].execute(ctx)
        remote.assert_called_once_with(ctx)
    assert not build_pipeline(ctx.config).availability(selected)["rendering"][0]


def test_mapping_preserves_card_fields_and_excludes_comments(tmp_path):
    ctx = context(tmp_path)
    card = ctx.cards[0]
    card.comment_text = "Private draft comment"
    payload = build_crucible_card(card, ctx.set_definition, ctx.config)
    assert payload["name"] == card.name
    assert payload["abilities"] == card.oracle_text
    assert payload["flavorText"] == card.flavor_text
    assert payload["typeLine"] == "Creature — Plant"
    assert payload["collectorNumber"] == card.collector_number
    assert payload["setCode"] == "PKM"
    assert payload["artist"] == "Tester"
    assert payload["manaCost"] == "{G}"
    assert "Private draft comment" not in json.dumps(payload)
    card.rarity = "land"
    assert build_crucible_card(card, ctx.set_definition, ctx.config)["rarity"] == "common"
    card.loyalty = "4"
    assert build_crucible_card(card, ctx.set_definition, ctx.config)["startingLoyalty"] == "4"


def test_multi_face_cards_are_rejected_instead_of_silently_dropped(tmp_path):
    ctx = context(tmp_path)
    ctx.cards[0].layout = "transform"
    with pytest.raises(ValueError, match="linked-face"):
        build_crucible_card(ctx.cards[0], ctx.set_definition, ctx.config)


def test_draft_footer_uses_card_id_and_cache_tracks_draft_mode(tmp_path):
    ctx = context(tmp_path)
    card = ctx.cards[0]
    card.id = "CG07"
    card.collector_number = "042"
    client = Mock(identity="runtime-v1")
    client.render.return_value = jpeg()
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient", return_value=client):
        ctx.set_definition.draft_mode = True
        render_crucible_cards(ctx)
        draft_key = card.render_cache_key
        payload = client.render.call_args.args[0]
        assert payload["custom_footer_field"] == "CG07"
        assert payload["collectorNumber"] == "042"
        ctx.set_definition.draft_mode = False
        render_crucible_cards(ctx)
        assert client.render.call_count == 2
        assert "custom_footer_field" not in client.render.call_args.args[0]
        assert card.render_cache_key != draft_key
        ctx.set_definition.draft_mode = True
        render_crucible_cards(ctx)
        assert client.render.call_count == 2
        assert card.render_cache_key == draft_key


@pytest.mark.parametrize("rarity, icon", [("common", "common"), ("uncommon", "uncommon"),
                                         ("rare", "rare"), ("mythic", "mythic"), ("land", "common")])
def test_custom_set_symbol_uses_matching_rarity_and_builtin_when_unset(tmp_path, rarity, icon):
    ctx = context(tmp_path)
    ctx.cards[0].rarity = rarity
    assert "setSymbol" not in build_crucible_card(ctx.cards[0], ctx.set_definition, ctx.config)
    for name in ("common", "uncommon", "rare", "mythic"):
        setattr(ctx.set_definition, f"set_icon_{name}", f"https://cdn.example/{name}.png")
    payload = build_crucible_card(ctx.cards[0], ctx.set_definition, ctx.config)
    assert payload["setSymbol"] == f"https://cdn.example/{icon}.png"


def test_set_symbol_changes_invalidate_render_cache_and_failures_do_not_save(tmp_path):
    ctx = context(tmp_path)
    client = Mock(identity="runtime-v1")
    client.render.return_value = jpeg()
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient", return_value=client):
        render_crucible_cards(ctx)
        original_key = ctx.cards[0].render_cache_key
        ctx.set_definition.set_icon_common = "https://cdn.example/common.png"
        render_crucible_cards(ctx)
        assert client.render.call_count == 2
        assert ctx.cards[0].render_cache_key != original_key
        assert client.render.call_args.args[0]["setSymbol"] == ctx.set_definition.set_icon_common
        render_crucible_cards(ctx)
        assert client.render.call_count == 2
        ctx.set_definition.set_icon_common = "https://cdn.example/broken.png"
        client.render.side_effect = RuntimeError("Invalid custom set symbol")
        files_before = set((tmp_path / ".cache/mtg_crucible/renders").iterdir())
        output = tmp_path / ctx.release_dir / "cards" / ctx.cards[0].expected_rendered_filename
        with pytest.raises(RuntimeError, match="Invalid custom set symbol"):
            render_crucible_cards(ctx)
        assert set((tmp_path / ".cache/mtg_crucible/renders").iterdir()) == files_before
        assert output.read_bytes() == jpeg()


def test_cache_reuse_invalidation_and_jpeg_contract(tmp_path):
    ctx = context(tmp_path)
    client = Mock(identity="runtime-v1")
    client.render.return_value = jpeg()
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient", return_value=client):
        outputs = render_crucible_cards(ctx)
        original_key = ctx.cards[0].render_cache_key
        assert outputs[0].name == ctx.cards[0].expected_rendered_filename
        assert outputs[0].read_bytes() == jpeg()  # No MTG.Design postprocessing applied.
        assert ctx.cards[0].artwork_public_url is None
        assert ctx.cards[0].rendered_image_path.startswith(".cache/mtg_crucible/")
        ctx.release_dir = Path("releases/0.2.0")
        ctx.set_definition.version = "0.2.0"
        render_crucible_cards(ctx)
        assert ctx.statistics["renderer"]["cache_hits"] == 1
        assert client.render.call_count == 1
        assert ctx.cards[0].render_cache_key == original_key
        ctx.cards[0].oracle_text += "\nFlying"
        render_crucible_cards(ctx)
        assert client.render.call_count == 2
        ctx.config.crucible.quality = "medium"
        render_crucible_cards(ctx)
        assert client.render.call_count == 3
        Image.new("RGB", (200, 140), "blue").save(tmp_path / "artwork/manual.png")
        render_crucible_cards(ctx)
        assert client.render.call_count == 4
        client.identity = "runtime-v2"
        render_crucible_cards(ctx)
        assert client.render.call_count == 5
        (tmp_path / ctx.cards[0].rendered_image_path).write_bytes(b"corrupt")
        render_crucible_cards(ctx)
        assert client.render.call_count == 6


def test_invalid_or_missing_art_is_not_saved(tmp_path):
    ctx = context(tmp_path)
    client = Mock(identity="runtime-v1")
    client.render.return_value = b"not a jpeg"
    with patch("mtg_pipeline.renderers.crucible.CrucibleRendererClient", return_value=client):
        with pytest.raises(ValueError):
            render_crucible_cards(ctx)
        assert not (tmp_path / ctx.release_dir / "cards").exists()
        assert ctx.statistics["renderer"]["failures"] == 1
        ctx.cards[0].artwork_path = "../escape.png"
        with pytest.raises(ValueError, match="prepared local artwork"):
            render_crucible_cards(ctx)


@pytest.mark.parametrize("value", ["..", "releases/cache", "."])
def test_cache_cannot_escape_project_or_overlap_releases(tmp_path, value):
    config = AppConfig()
    config.cache.crucible = Path(value)
    with pytest.raises(ValueError):
        cache_root(config, tmp_path)


def test_bridge_handles_timeout_and_invalid_output(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    runtime = tmp_path / ".cache/mtg_crucible/runtime"
    (runtime / "dist").mkdir(parents=True)
    (runtime / "dist/index.js").write_text("module.exports={}")
    (runtime / "ready.json").write_text(json.dumps({"fingerprint": "runtime-v1", "repository": str((tmp_path / ctx.config.crucible.repository_path).resolve())}))
    monkeypatch.setattr("mtg_pipeline.renderers.crucible.node_path", lambda *args: "node")
    client = CrucibleRendererClient(ctx.config, tmp_path)
    ctx.set_definition.set_icon_common = "https://cdn.example/common.png"
    payload = build_crucible_card(ctx.cards[0], ctx.set_definition, ctx.config)
    art = tmp_path / ctx.cards[0].artwork_path
    with patch("mtg_pipeline.renderers.crucible.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 120)):
        with pytest.raises(RuntimeError, match="timed out"):
            client.render(payload, art)
    with patch("mtg_pipeline.renderers.crucible.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="invalid")):
        with pytest.raises(RuntimeError, match="invalid image response"):
            client.render(payload, art)
    with patch("mtg_pipeline.renderers.crucible.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=json.dumps({"image":base64.b64encode(jpeg()).decode()}))) as run:
        assert client.render(payload, art) == jpeg()
        request = json.loads(run.call_args.kwargs["input"])
        assert request["card"] == payload
        assert base64.b64decode(request["artBase64"]) == art.read_bytes()
        assert "shell" not in run.call_args.kwargs


def test_ui_renderer_switch_preserves_legacy_option_and_dependencies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with source_mocks(sample_source()), patch("mtg_pipeline.artwork.r2.r2_settings", return_value=("", "", False)):
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        engine = next(field for field in app.selectbox if field.label == "Rendering engine")
        assert engine.value == "crucible"
        assert not app.checkbox(key="step_artwork_r2_upload").value
        assert app.checkbox(key="step_rendering").value
        assert not app.checkbox(key="step_rendering").disabled
        session = next(field for field in app.text_input if field.label == "MTG.Design Laravel Session")
        assert session.disabled
        engine.select("mtg_design").run()
        assert not next(field for field in app.text_input if field.label == "MTG.Design Laravel Session").disabled
        # No R2 credentials in this test environment: only the remote renderer is unavailable.
        assert app.checkbox(key="step_rendering").disabled
        assert not app.exception
