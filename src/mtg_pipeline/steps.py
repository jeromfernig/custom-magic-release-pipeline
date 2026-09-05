from __future__ import annotations

from pathlib import Path

from mtg_pipeline.artwork.preparer import ArtworkPreparer
from mtg_pipeline.canonicalize import canonicalize, write_canonical_json
from mtg_pipeline.config import AppConfig
from mtg_pipeline.exporters.cockatrice import export_cockatrice
from mtg_pipeline.exporters.cubecobra import export_cubecobra
from mtg_pipeline.exporters.draftmancer import export_draftmancer
from mtg_pipeline.exporters.print_pdf import export_print_pdf
from mtg_pipeline.manifest import write_manifest
from mtg_pipeline.pipeline import PipelineContext, PipelineRunResult, PipelineRunner, PipelineStep
from mtg_pipeline.renderers.mtg_card_renderer import render_selected_cards
from mtg_pipeline.sources.google_sheets import GoogleSheetsSource
from mtg_pipeline.utils.filenames import slugify
from mtg_pipeline.validation import validate_source, write_validation_report


def build_pipeline() -> PipelineRunner:
    return PipelineRunner([
        PipelineStep("source", "Import source", "Read current data from Google Sheets.", tuple(), _source, mandatory=True),
        PipelineStep("validation", "Validate source", "Validate set metadata and card rows.", ("source",), _validation, mandatory=True),
        PipelineStep("canonicalization", "Create canonical model", "Write typed set/card JSON.", ("validation",), _canonicalization, mandatory=True),
        PipelineStep("artwork", "Prepare artwork", "Load, generate, or substitute card artwork.", ("canonicalization",), _artwork),
        PipelineStep("rendering", "Render cards", "Render cards through mtg-card-renderer.", ("artwork",), _rendering),
        PipelineStep("print", "Generate print PDF", "Create A4 4x2 printable sheets.", ("rendering",), _print),
        PipelineStep("draftmancer", "Generate Draftmancer file", "Create Draftmancer custom-card import file.", ("canonicalization",), _draftmancer),
        PipelineStep("cubecobra", "Generate CubeCobra CSV", "Create practical manual-import CSV.", ("canonicalization",), _cubecobra),
        PipelineStep("cockatrice", "Generate Cockatrice XML", "Create Cockatrice v4 custom set XML.", ("canonicalization",), _cockatrice),
        PipelineStep("r2_upload", "Upload to Cloudflare R2", "Future image hosting step.", ("rendering",), lambda ctx: [], poc_available=False),
    ])


def finalize_manifest(context: PipelineContext, selected: set[str], run_result: PipelineRunResult) -> Path | None:
    if not context.set_definition or context.release_dir is None:
        return None
    write_canonical_json(context.set_definition, context.cards, context.release_dir)
    return write_manifest(context.set_definition, context.cards, selected, run_result, context.release_dir)


def _source(context: PipelineContext) -> list[Path]:
    context.source_data = GoogleSheetsSource(context.config.source, progress=context.report_progress).load()
    return []


def _validation(context: PipelineContext) -> list[Path]:
    if context.source_data is None:
        raise RuntimeError("Validation requires source data.")
    context.validation_result = validate_source(context.source_data, context.config)
    set_id = slugify(str(context.source_data.set_metadata.get("set_id", "unknown-set")).strip() or "unknown-set")
    version = str(context.source_data.set_metadata.get("version", "development")).strip() or "development"
    context.release_dir = context.config.paths.release_root / set_id / version
    context.release_dir.mkdir(parents=True, exist_ok=True)
    report = write_validation_report(context.validation_result, context.release_dir / "validation-report.txt")
    context.warnings.extend(issue.message for issue in context.validation_result.warnings)
    if not context.validation_result.ok:
        raise RuntimeError(f"Validation failed with {len(context.validation_result.errors)} error(s). See {report}.")
    return [report]


def _canonicalization(context: PipelineContext) -> list[Path]:
    if context.source_data is None or context.release_dir is None:
        raise RuntimeError("Canonicalization requires validated source data.")
    set_def, cards = canonicalize(context.source_data, context.config)
    context.set_definition = set_def
    context.cards = cards
    return write_canonical_json(set_def, cards, context.release_dir)


def _artwork(context: PipelineContext) -> list[Path]:
    result = ArtworkPreparer(context.config, context.project_root, progress=context.report_progress).prepare(context.cards, _set_def(context).set_id)
    context.warnings.extend(result.warnings)
    source = GoogleSheetsSource(context.config.source, progress=context.report_progress)
    for index, update in enumerate(result.generated_updates, start=1):
        context.report_progress(f"Saving artwork reference {index}/{len(result.generated_updates)} to sheet row {update.row_number}")
        try:
            source.update_generated_artwork_fields(
                context.config.source.cards_worksheet,
                context.config.columns,
                update.row_number,
                update.artwork_path,
                update.artist_credit,
            )
        except Exception as exc:
            context.warnings.append(f"Generated artwork was saved but Google Sheet update failed for row {update.row_number}: {exc}")
    return result.output_files


def _rendering(context: PipelineContext) -> list[Path]:
    return render_selected_cards(_set_def(context), context.cards, context.config, _release_dir(context), context.project_root, context.report_progress)


def _print(context: PipelineContext) -> list[Path]:
    set_def = _set_def(context)
    output = _release_dir(context) / "print" / f"{set_def.set_code.lower()}-{set_def.version}.pdf"
    return [export_print_pdf(set_def, context.cards, _release_dir(context), context.config.print, output)]


def _draftmancer(context: PipelineContext) -> list[Path]:
    set_def = _set_def(context)
    _warn_if_public_urls_placeholder(context)
    output = _release_dir(context) / "draftmancer" / f"{set_def.set_code.lower()}-{set_def.version}.txt"
    return [export_draftmancer(set_def, context.cards, context.config, output)]


def _cubecobra(context: PipelineContext) -> list[Path]:
    set_def = _set_def(context)
    _warn_if_public_urls_placeholder(context)
    output = _release_dir(context) / "cubecobra" / f"{set_def.set_code.lower()}-{set_def.version}.csv"
    return [export_cubecobra(set_def, context.cards, context.config, output)]


def _cockatrice(context: PipelineContext) -> list[Path]:
    set_def = _set_def(context)
    _warn_if_public_urls_placeholder(context)
    output = _release_dir(context) / "cockatrice" / f"{set_def.set_code.lower()}-{set_def.version}.xml"
    return [export_cockatrice(set_def, context.cards, context.config, output)]


def _release_dir(context: PipelineContext) -> Path:
    if context.release_dir is None:
        raise RuntimeError("Release directory has not been initialized.")
    return context.release_dir


def _set_def(context: PipelineContext):
    if context.set_definition is None:
        raise RuntimeError("Canonical set definition is not available.")
    return context.set_definition


def _warn_if_public_urls_placeholder(context: PipelineContext) -> None:
    base = context.config.images.public_image_base_url
    if not base or "example.com" in base:
        message = "Public image URLs are placeholders until rendered images are manually hosted or R2 upload is implemented."
        if message not in context.warnings:
            context.warnings.append(message)
