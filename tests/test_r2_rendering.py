from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from PIL import Image
from botocore.exceptions import ClientError, ConnectionClosedError

from mtg_pipeline.artwork.r2 import CloudflareR2Client, publish_source_artwork
from mtg_pipeline.canonicalize import canonicalize
from mtg_pipeline.config import AppConfig, R2Config
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.renderers.mtg_design import (
    AUTH_ERROR, MtgDesignRendererClient, build_mtg_design_params,
    normalize_params, render_design_cards, request_hash, validate_jpeg, _html_response_error, _set_symbol,
)
from mtg_pipeline.steps import build_pipeline
from tests.fixtures import sample_source


def jpeg():
    output = BytesIO()
    Image.new("RGB", (100, 140), "green").save(output, format="JPEG")
    return output.getvalue()


def context(tmp_path):
    source = sample_source()
    source.card_rows = source.card_rows[:1]
    config = AppConfig()
    set_def, cards = canonicalize(source, config)
    art = tmp_path / "artwork/manual.png"
    art.parent.mkdir()
    art.write_bytes(jpeg())  # Deliberately mismatched extension.
    cards[0].artwork_path = "artwork/manual.png"
    ctx = PipelineContext(config=config, set_definition=set_def, cards=cards,
        project_root=tmp_path, release_dir=tmp_path / "releases/0.4.0")
    with patch.object(CloudflareR2Client, "__init__", return_value=None), patch.object(
        CloudflareR2Client, "upload_file", return_value=True
    ), patch.object(CloudflareR2Client, "public_url", side_effect=lambda key: "https://cdn.example/" + key):
        publish_source_artwork(cards, config, tmp_path, lambda message: None, ctx.statistics)
    return ctx


def test_artwork_hash_mapping_and_cross_version_cache(tmp_path):
    ctx = context(tmp_path)
    ctx.config.mtg_design.postprocess.enabled = False
    card = ctx.cards[0]
    ctx.set_definition.set_icon_common = "https://cdn.example/icons/common.png"
    assert card.artwork_object_key == f"artwork/{card.artwork_sha256}.jpg"
    assert card.artwork_url.startswith("https://example.com/")
    params = build_mtg_design_params(card, ctx.set_definition, ctx)
    assert params["artwork"] == card.artwork_public_url
    assert params["type"] == "Creature" and params["sub-type"] == "Plant"
    assert params["rarity"] == "C"
    assert params["set-symbol"] == "https://cdn.example/icons/common.png"
    assert params["edit"] == "false"
    key = request_hash(params)
    assert request_hash(normalize_params({"x": "a\r\nb", "b": False})) == request_hash({"b": "false", "x": "a\nb"})
    for field in params:
        assert request_hash({**params, field: params[field] + "changed"}) != key
    with patch.object(MtgDesignRendererClient, "render", return_value=jpeg()) as render:
        render_design_cards(ctx)
        ctx.set_definition.version = "0.5.0"
        ctx.release_dir = tmp_path / "releases/0.5.0"
        ctx.mtg_design_session = "secret-not-in-hash"
        render_design_cards(ctx)
        assert render.call_count == 1
        assert ctx.statistics["renderer"]["cache_hits"] == 1
        assert request_hash(build_mtg_design_params(card, ctx.set_definition, ctx)) == key
        assert (ctx.release_dir / "cards" / card.expected_rendered_filename).read_bytes() == jpeg()
        assert "secret-not-in-hash" not in (tmp_path / ".cache/mtg_design/metadata" / f"{key}.json").read_text()
        (tmp_path / card.rendered_image_path).write_bytes(b"corrupt")
        render_design_cards(ctx)
        assert render.call_count == 2
    card.artwork_public_url = "https://cdn.example/artwork/changed.jpg"
    assert request_hash(build_mtg_design_params(card, ctx.set_definition, ctx)) != key


def test_mtg_design_never_uses_comment_text_as_flavor_text(tmp_path):
    ctx = context(tmp_path)
    card = ctx.cards[0]
    ctx.set_definition.draft_mode = True
    card.flavor_text = None
    card.comment_text = "Internal card-development note."

    assert build_mtg_design_params(card, ctx.set_definition, ctx)["flavor-text"] == ""


@pytest.mark.parametrize(
    ("rarity", "expected"),
    [("common", "common"), ("uncommon", "uncommon"), ("rare", "rare"), ("mythic", "mythic"), ("land", "common")],
)
def test_set_symbol_uses_rarity_specific_set_icon(tmp_path, rarity, expected):
    ctx = context(tmp_path)
    for icon_rarity in ("common", "uncommon", "rare", "mythic"):
        setattr(ctx.set_definition, f"set_icon_{icon_rarity}", f"https://cdn.example/icons/{icon_rarity}.png")

    assert _set_symbol(ctx.set_definition, rarity) == f"https://cdn.example/icons/{expected}.png"


def test_rules_text_size_is_reduced_and_warns_below_readability_threshold(tmp_path):
    ctx = context(tmp_path)
    card = ctx.cards[0]
    card.oracle_text = (
        "Evolve from Machop {3}{W} ({3}{W}: Exile target Machop you control. If you do, put this card onto "
        "the battlefield from your hand. It gains all abilities of creatures it evolved from and gains haste. "
        "Activate only as a sorcery.)\nVigilance\n<i>Valiant</i> - Whenever this creature becomes the target of a "
        "spell or ability you control for the first time each turn, until end of turn, whenever one or more "
        "creatures you control attack, those creatures get +1/+1 until end of turn."
    )

    with patch.object(MtgDesignRendererClient, "render", return_value=jpeg()) as render:
        render_design_cards(ctx)

    assert render.call_args.args[0]["text-size"] == "28"
    assert any("text size 28" in warning for warning in ctx.warnings)


@pytest.mark.parametrize("present", [True, False])
def test_r2_upload_or_reuse(tmp_path, present):
    from mtg_pipeline.utils.cache import file_digest
    path = tmp_path / "art.jpg"
    path.write_bytes(jpeg())
    client = CloudflareR2Client.__new__(CloudflareR2Client)
    client.bucket, client.public_base, client.client = "bucket", "https://cdn.example", Mock()
    if present:
        client.client.head_object.return_value = {"Metadata": {"sha256": file_digest(path)}, "ContentLength": path.stat().st_size}
    else:
        client.client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert client.upload_file(path, "artwork/hash.jpg", "image/jpeg") is not present
    assert client.client.put_object.call_count == int(not present)
    assert client.public_url("artwork/hash.jpg") == "https://cdn.example/artwork/hash.jpg"


def test_r2_client_uses_r2_compatible_s3_settings(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "test-secret-key")

    client = CloudflareR2Client(R2Config(bucket="custom-mtg-art", public_url="https://cdn.example"))

    assert client.client.meta.config.signature_version == "s3v4"
    assert client.client.meta.config.s3 == {"addressing_style": "path", "payload_signing_enabled": False}
    assert client.client.meta.config.request_checksum_calculation == "when_required"
    assert client.client.meta.config.retries["total_max_attempts"] == 3
    client.client.close()


def test_r2_transport_failure_reopens_file_and_retries(tmp_path):
    path = tmp_path / "art.jpg"
    path.write_bytes(jpeg())
    client = CloudflareR2Client.__new__(CloudflareR2Client)
    client.bucket, client.public_base, client.client = "bucket", "https://cdn.example", Mock()
    client.client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    client.client.put_object.side_effect = [
        ConnectionClosedError(endpoint_url="https://example.invalid"),
        {},
    ]

    with patch("mtg_pipeline.artwork.r2.time.sleep") as sleep:
        assert client.upload_file(path, "artwork/hash.jpg", "image/jpeg") is True

    assert client.client.put_object.call_count == 2
    assert sleep.call_args.args == (1,)
    first_body = client.client.put_object.call_args_list[0].kwargs["Body"]
    second_body = client.client.put_object.call_args_list[1].kwargs["Body"]
    assert first_body is not second_body


def test_r2_upload_error_preserves_service_details(tmp_path):
    path = tmp_path / "art.jpg"
    path.write_bytes(jpeg())
    client = CloudflareR2Client.__new__(CloudflareR2Client)
    client.bucket, client.public_base, client.client = "custom-mtg-art", "https://cdn.example", Mock()
    client.client.head_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Access Denied"},
         "ResponseMetadata": {"RequestId": "abc123"}},
        "HeadObject",
    )

    with pytest.raises(RuntimeError, match="checking whether the object already exists.*AccessDenied.*abc123"):
        client.upload_file(path, "artwork/hash.jpg", "image/jpeg")


@pytest.mark.parametrize("status,kind,body", [(302, "text/html", b""), (200, "text/html", b"<html>Login</html>"),
    (200, "image/jpeg", b"invalid")])
def test_reject_login_and_non_jpeg(status, kind, body):
    client = MtgDesignRendererClient(AppConfig().mtg_design, "private-token")
    with patch.object(client.session, "get", return_value=Mock(status_code=status,
        headers={"Content-Type": kind}, url="https://mtg.design/render", content=body)) as get:
        with pytest.raises((RuntimeError, ValueError)) as error:
            client.render({})
        assert "private-token" not in str(error.value)
        get.assert_called_once()
    client.close()


def test_accepts_valid_jpeg_with_incorrect_content_type() -> None:
    client = MtgDesignRendererClient(AppConfig().mtg_design, "private-token")
    response = Mock(status_code=200, headers={"Content-Type": "text/html"},
                    url="https://mtg.design/render", content=jpeg())
    with patch.object(client.session, "get", return_value=response):
        assert client.render({}) == jpeg()
    client.close()


def test_html_response_distinguishes_login_from_render_error() -> None:
    login_error = _html_response_error(b'<html><title>Login</title><form action="/login"><input name="email"></form></html>')
    assert login_error.startswith(AUTH_ERROR)
    assert '<form action="/login">' in login_error
    error = _html_response_error(b'<html><title>Render failed</title><h1>Artwork could not be retrieved</h1></html>')
    assert error.startswith("MTG.Design returned an HTML error page instead of a JPEG: Render failed")
    assert "Artwork could not be retrieved" in error


def test_request_pacing_retry_and_exact_jpeg():
    client = MtgDesignRendererClient(AppConfig().mtg_design, "private-token")
    good = Mock(status_code=200, headers={"Content-Type": "image/jpeg"}, url="https://mtg.design/render", content=jpeg())
    retry = Mock(status_code=503, headers={"Retry-After": "10"}, url="https://mtg.design/render")
    with patch.object(client.session, "get", side_effect=[retry, good, good]) as get, patch(
        "mtg_pipeline.renderers.mtg_design.time.monotonic", return_value=10
    ), patch("mtg_pipeline.renderers.mtg_design.time.sleep") as sleep:
        assert client.render({"edit": "false"}) == jpeg()
        assert client.render({"edit": "false"}) == jpeg()
        assert get.call_count == 3
        assert 10 in [call.args[0] for call in sleep.call_args_list]
        assert 2 in [call.args[0] for call in sleep.call_args_list]
        assert client.session.cookies.get("laravel_session") == "private-token"
    client.close()
    assert validate_jpeg(jpeg()) == jpeg()
    with pytest.raises(RuntimeError, match="authentication failed"):
        MtgDesignRendererClient(AppConfig().mtg_design).render({})


def test_dependency_propagation():
    config = AppConfig()
    config.rendering.engine = "mtg_design"
    runner = build_pipeline(config)
    selected = {step.id for step in runner.steps}
    for disabled in ("artwork_r2_upload", "rendering"):
        states = runner.availability(selected - {disabled})
        assert not states["print"][0]
        assert not states["rendered_card_r2_upload"][0]
        assert not states["draftmancer"][0]
    assert not runner.availability(selected, {"artwork_r2_upload": "Missing credentials"})["rendering"][0]
