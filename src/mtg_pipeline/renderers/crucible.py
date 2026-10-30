from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

from mtg_pipeline.artwork.generation import safe_error
from mtg_pipeline.renderers.mtg_design import SET_ICON_FIELDS, validate_jpeg
from mtg_pipeline.utils.cache import atomic_write, file_digest, write_json
from mtg_pipeline.linked_cards import physical_cards, linked_face, validate_links


def cache_root(config, project_root: Path) -> Path:
    root = project_root.resolve()
    path = (root / config.cache.crucible).resolve()
    if path == root or not path.is_relative_to(root) or path.is_relative_to((root / config.paths.release_root).resolve()):
        raise ValueError("Crucible cache must be inside the project and outside releases.")
    return path


def node_path(config, project_root: Path) -> str:
    requested = config.crucible.node_executable
    if path := shutil.which(requested):
        return path
    candidate = (project_root / requested).resolve()
    if candidate.is_file():
        return str(candidate)
    if requested == "node":
        portable = sorted((project_root / ".cache/node").glob("node-*-win-x64/node.exe"))
        if portable:
            return str(portable[-1].resolve())
    raise RuntimeError("Node.js is not available. Install Node.js or run: python -m mtg_pipeline.setup_crucible --download-node")


def build_crucible_card(card, set_def, config) -> dict:
    if card.layout != "normal" and not (card.link_id and card.layout == card.link_type):
        raise ValueError(f"Crucible: {card.name} needs linked-face data for layout {card.layout!r}; only single-face cards are mapped.")
    profile = config.render_profiles.get(card.render_profile)
    if profile is None:
        raise ValueError(f"Unknown render profile: {card.render_profile}")
    options = profile.crucible_options
    allowed = {"cardTemplate", "frameColor", "accentColor", "frameEffect", "nameLineColor", "typeLineColor", "ptBoxColor", "colorIndicator", "legendCrown"}
    if set(options) - allowed:
        raise ValueError(f"Unsupported Crucible profile options: {', '.join(sorted(set(options) - allowed))}")
    result = {
        "name": card.name, "manaCost": card.mana_cost or "", "typeLine": card.type_line.replace(" - ", " — ", 1),
        "abilities": card.oracle_text or "", "flavorText": card.flavor_text or "",
        "rarity": "common" if card.rarity == "land" else card.rarity,
        "collectorNumber": card.collector_number, "artist": card.artist or "",
        "setCode": set_def.set_code, "language": "EN", "designer": config.crucible.designer,
    }
    if set_def.draft_mode:
        result["custom_footer_field"] = card.id
    if symbol := getattr(set_def, SET_ICON_FIELDS[card.rarity], None):
        result["setSymbol"] = symbol
    if card.color_indicator:
        result["colorIndicator"] = [{"W": "white", "U": "blue", "B": "black", "R": "red", "G": "green"}[color] for color in card.color_indicator]
    if card.battle_defense:
        result["battleDefense"] = card.battle_defense
    for source, target in (("power", "power"), ("toughness", "toughness"), ("loyalty", "startingLoyalty")):
        if (value := getattr(card, source)) is not None:
            result[target] = value
    result.update(options)
    return result


class CrucibleRendererClient:
    def __init__(self, config, project_root: Path) -> None:
        self.config = config
        self.root = project_root
        self.runtime = cache_root(config, project_root) / "runtime"
        marker = self.runtime / "ready.json"
        if not marker.is_file() or not (self.runtime / "dist/index.js").is_file():
            raise RuntimeError("Crucible runtime is not prepared. Run: python -m mtg_pipeline.setup_crucible")
        manifest = json.loads(marker.read_text(encoding="utf-8"))
        expected_source = (project_root / config.crucible.repository_path).resolve()
        if Path(manifest.get("repository", "")).resolve() != expected_source:
            raise RuntimeError("Crucible runtime belongs to a different clone. Update crucible.repository_path in YAML and rerun: python -m mtg_pipeline.setup_crucible")
        self.identity = manifest["fingerprint"]
        self.node = node_path(config, project_root)

    def render(self, data: dict, art: Path) -> bytes:
        front, back = self.render_faces(data, art)
        if back is not None:
            raise RuntimeError("Unexpected back face in a single-image render.")
        return front

    def render_faces(self, data: dict, art: Path, linked_art: Path | None = None) -> tuple[bytes, bytes | None]:
        from PIL import Image

        # Re-encode raster art without provider metadata for Skia/canvas compatibility.
        # This never changes the source artwork file or its pixels/dimensions.
        with Image.open(art) as image:
            image.load()
            normalized = BytesIO()
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(normalized, format="PNG")
        request = {"modulePath": str((self.runtime / "dist/index.js").resolve()), "card": data,
                   "artBase64": base64.b64encode(normalized.getvalue()).decode("ascii"), "quality": self.config.crucible.quality}
        if linked_art:
            with Image.open(linked_art) as image:
                image.load()
                normalized_back = BytesIO()
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(normalized_back, format="PNG")
            request["linkedArtBase64"] = base64.b64encode(normalized_back.getvalue()).decode("ascii")
        try:
            result = subprocess.run([self.node, str(Path(__file__).with_name("crucible_bridge.cjs"))],
                                    input=json.dumps(request, ensure_ascii=False), capture_output=True,
                                    encoding="utf-8", timeout=self.config.crucible.timeout_seconds, check=False)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Crucible render timed out; no image was saved.") from None
        if result.returncode:
            raise RuntimeError("Crucible render failed: " + safe_error(result.stderr.strip()[-1000:]))
        try:
            response = json.loads(result.stdout)
            def decode(value):
                content = base64.b64decode(value, validate=True)
                with Image.open(BytesIO(content)) as image:
                    image.load()
                    if image.format != "JPEG":
                        raise ValueError("Expected JPEG")
                    if image.width > image.height:
                        # A landscape battle occupies a normal portrait physical card.
                        output = BytesIO()
                        image.transpose(Image.Transpose.ROTATE_90).save(output, format="JPEG", quality=95)
                        content = output.getvalue()
                return validate_jpeg(content)
            return decode(response["image"]), decode(response["back"]) if response.get("back") else None
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError("Crucible returned an invalid image response; no image was saved.") from None


def render_crucible_cards(context):
    from PIL import Image

    root = context.project_root.resolve()
    if context.release_dir is None or context.set_definition is None:
        raise RuntimeError("Crucible rendering requires canonical card data and a release directory.")
    cache = cache_root(context.config, root)
    client = CrucibleRendererClient(context.config, root)
    config = context.config.crucible
    validate_links(context.cards)
    cards = physical_cards(context.cards)
    cards = cards[:config.render_limit] if config.render_limit else cards
    stats = {"name": "mtg-crucible", "cards_processed": 0, "cache_hits": 0, "local_renders": 0, "failures": 0}
    context.statistics["renderer"] = stats
    outputs = []
    try:
        for index, card in enumerate(cards, 1):
            art = (root / (card.artwork_path or "")).resolve()
            if not card.artwork_path or not art.is_relative_to(root) or not art.is_file():
                raise ValueError(f"Crucible needs prepared local artwork for {card.name}.")
            with Image.open(art) as image:
                image.verify()
            data = build_crucible_card(card, context.set_definition, context.config)
            face = linked_face(card, context.cards)
            back_art = None
            if face:
                data["linkType"] = card.link_type
                data["linkedCard"] = build_crucible_card(face, context.set_definition, context.config)
                if card.link_type == "transform":
                    back_art = (root / (face.artwork_path or "")).resolve()
                    if not face.artwork_path or not back_art.is_relative_to(root) or not back_art.is_file():
                        raise ValueError(f"Crucible needs prepared local artwork for {face.name}.")
            identity = {"renderer": "mtg-crucible", "runtime": client.identity, "adapter": file_digest(Path(__file__)), "bridge": file_digest(Path(__file__).with_name("crucible_bridge.cjs")),
                        "quality": config.quality, "format": "jpeg", "card": data, "artwork_sha256": file_digest(art)}
            if back_art:
                identity["linked_artwork_sha256"] = file_digest(back_art)
            key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
            cached = cache / "renders" / f"{key}.jpg"
            cached_back = cache / "renders" / f"{key}-back.jpg"
            back_content = None
            try:
                content = validate_jpeg(cached.read_bytes())
                if back_art:
                    back_content = validate_jpeg(cached_back.read_bytes())
            except (OSError, ValueError):
                content = None
            context.report_progress(f"Rendering {index}/{len(cards)}: {card.name} - {'cache hit' if content else 'local Crucible render'}")
            if content is None:
                if back_art:
                    content, back_content = client.render_faces(data, art, back_art)
                    content = validate_jpeg(content)
                    if back_content is None:
                        raise RuntimeError(f"Crucible returned no back face for {card.id}.")
                    back_content = validate_jpeg(back_content)
                else:
                    content = validate_jpeg(client.render(data, art))
                if back_content:
                    atomic_write(cached_back, back_content)
                atomic_write(cached, content)
                write_json(cache / "metadata" / f"{key}.json", identity)
                stats["local_renders"] += 1
            else:
                stats["cache_hits"] += 1
            output = root / context.release_dir / "cards" / card.expected_rendered_filename
            atomic_write(output, content)
            card.rendered_image_filename = output.name
            card.rendered_image_path = cached.relative_to(root).as_posix()
            card.render_cache_key = key
            card.rendered_public_url = None
            outputs.append(output)
            if face:
                if back_content:
                    face_output = root / context.release_dir / "cards" / face.expected_rendered_filename
                    atomic_write(face_output, back_content)
                    face.rendered_image_path = cached_back.relative_to(root).as_posix()
                    face.rendered_public_url = None
                    outputs.append(face_output)
                else:
                    # A prepared spell is an inset on the main card's single image.
                    face.rendered_image_filename = card.rendered_image_filename
                    face.rendered_image_path = card.rendered_image_path
                    face.rendered_public_url = None
                face.render_cache_key = key
            stats["cards_processed"] += 1
    except Exception:
        stats["failures"] += 1
        raise
    finally:
        context.report_progress(f"Rendering summary: {stats}")
    return outputs
