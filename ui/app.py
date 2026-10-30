from __future__ import annotations

import logging
import json
from copy import deepcopy
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path

import streamlit as st
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mtg_pipeline.config import load_config
from mtg_pipeline.artwork.r2 import r2_settings, validate_public_url
from mtg_pipeline.validation import validate_source
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.sources.google_sheets import GoogleSheetsSource
from mtg_pipeline.sources.sheet_history import load_sheet_history, normalize_spreadsheet_id, parse_sheet_option, save_sheet_history, sheet_options
from mtg_pipeline.steps import build_pipeline, finalize_manifest
from mtg_pipeline.exporters.draftmancer import RARITY_SHEETS, pack_layout_errors

DEFAULT_SPREADSHEET_ID = "10TNw2O_PG9JvWq10ARrYtN0JRjPJceh8rgRPiFbu7G4"


class StreamlitLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.buffer = StringIO()

    def emit(self, record: logging.LogRecord) -> None:
        self.buffer.write(self.format(record) + "\n")


def main() -> None:
    st.set_page_config(page_title="Custom Magic Release Pipeline", layout="wide")
    st.title("Custom Magic Release Pipeline")

    config_path = Path(st.sidebar.text_input("Config file", "config.example.yaml"))
    config = load_config(config_path)

    with st.sidebar.expander("Artwork configuration"):
        config.images.generation_provider = st.selectbox(
            "Image provider", ["openai", "gemini"],
            index=["openai", "gemini"].index(config.images.generation_provider),
        )
        config.images.default_art_folder = st.text_input("Default art folder", config.images.default_art_folder)
        config.images.gemini_model = st.text_input("Gemini image model", config.images.gemini_model)
        st.caption("Blank artist: OpenAI → Gemini → default art folder → white error image. Selecting Gemini starts with Gemini.")

    st.header("Source")
    history_path = ROOT / ".sheet-history.json"
    history = load_sheet_history(history_path)
    default_sheet_id = config.source.spreadsheet_id or DEFAULT_SPREADSHEET_ID
    history.setdefault(default_sheet_id, "mtg_pokemon_set" if default_sheet_id == DEFAULT_SPREADSHEET_ID else "Configured sheet")
    options = ["Enter manually", *sheet_options(history)]
    default_index = next(index for index, option in enumerate(options) if parse_sheet_option(option) == default_sheet_id)
    selected_history = st.selectbox("Google Sheet ID", options, index=default_index)
    if selected_history != "Enter manually":
        spreadsheet_id = parse_sheet_option(selected_history)
        st.caption(spreadsheet_id)
        history_name_default = history.get(spreadsheet_id, "")
    else:
        spreadsheet_id = st.text_input("Google Sheet ID", config.source.spreadsheet_id)
        history_name_default = ""
    try:
        normalized_spreadsheet_id = normalize_spreadsheet_id(spreadsheet_id)
    except ValueError as exc:
        normalized_spreadsheet_id = ""
        st.error(str(exc))
    if normalized_spreadsheet_id and normalized_spreadsheet_id != spreadsheet_id:
        st.caption(f"Using extracted spreadsheet ID: {normalized_spreadsheet_id}")
    config.source.spreadsheet_id = normalized_spreadsheet_id
    history_name = st.text_input("History display name", history_name_default, placeholder="mtg_pokemon_set")
    if st.button("Save sheet ID"):
        try:
            save_sheet_history(history_path, spreadsheet_id, history_name)
            st.success("Saved Google Sheet ID to local history.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    col1, col2 = st.columns(2)
    with col1:
        config.source.set_worksheet = st.text_input("Set worksheet", config.source.set_worksheet)
    with col2:
        config.source.cards_worksheet = st.text_input("Cards worksheet", config.source.cards_worksheet)
    source = GoogleSheetsSource(config.source)
    source_selection = (str(config_path.resolve()), normalized_spreadsheet_id, config.source.set_worksheet, config.source.cards_worksheet)
    if st.session_state.get("source_selection") != source_selection:
        st.session_state["source_selection"] = source_selection
        for key in ("loaded_source", "loaded_validation", "source_load_attempted", "source_load_error"):
            st.session_state.pop(key, None)

    oauth_session = st.session_state.get("google_oauth_session")
    if oauth_session:
        try:
            if source.finish_authentication(oauth_session):
                st.session_state.pop("google_oauth_session", None)
                st.session_state.pop("source_load_attempted", None)
                st.success("Google authentication token created.")
                st.rerun()
        except Exception as exc:
            st.session_state.pop("google_oauth_session", None)
            st.error(str(exc))

    token_status = source.token_status()
    st.write("Authentication token:", token_status.state)
    if token_status.state == "valid":
        st.caption("Using local token.json. Re-authentication is only needed if the token is deleted, revoked, expired, or loses access.")
    else:
        st.warning(token_status.message)
        if st.button("Reauthenticate with Google"):
            try:
                old_session = st.session_state.get("google_oauth_session")
                if old_session:
                    old_session.close()
                st.session_state["google_oauth_session"] = source.begin_authentication(reset_token=True)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    oauth_session = st.session_state.get("google_oauth_session")
    if oauth_session:
        st.link_button("Open Google authorization", oauth_session.auth_url)
        st.caption("After granting access, return here and click the button below.")
        if st.button("I've completed Google authentication"):
            st.rerun()

    load_requested = st.button("Load source")
    can_auto_load = bool(normalized_spreadsheet_id) and token_status.state in {"valid", "expired"} and not oauth_session
    if load_requested or (can_auto_load and not st.session_state.get("source_load_attempted")):
        st.session_state["source_load_attempted"] = True
        for key in ("loaded_source", "loaded_validation", "source_load_error"):
            st.session_state.pop(key, None)
        try:
            source_activity = st.empty()
            source.progress = source_activity.caption
            with st.spinner("Loading source sheet..."):
                loaded = source.load()
            source_activity.empty()
            st.session_state["loaded_source"] = loaded.model_dump()
            validation = validate_source(loaded, config)
            st.session_state["loaded_validation"] = validation.model_dump()
            sheet_name = history_name or source.spreadsheet_title(normalized_spreadsheet_id) or str(loaded.set_metadata.get("set_name", "unknown"))
            save_sheet_history(history_path, normalized_spreadsheet_id, sheet_name)
            st.success(f"Loaded {loaded.set_metadata.get('set_name', 'set')} version {loaded.set_metadata.get('version', 'unknown')}.")
        except Exception as exc:
            st.session_state["source_load_error"] = str(exc)
    if st.session_state.get("source_load_error"):
        st.error(st.session_state["source_load_error"])

    if "loaded_source" in st.session_state:
        loaded_source = st.session_state["loaded_source"]
        st.write("Detected set", loaded_source.get("set_metadata", {}).get("set_name", ""))
        st.write("Detected version", loaded_source.get("set_metadata", {}).get("version", ""))
        st.write("Detected cards", len(loaded_source.get("card_rows", [])))

    pack_errors = _edit_pack_layout(config, config_path)

    st.header("Rendering")
    config.rendering.engine = st.selectbox(
        "Rendering engine", ["crucible", "mtg_design"],
        index=["crucible", "mtg_design"].index(config.rendering.engine),
        format_func=lambda value: "MTG Crucible (local)" if value == "crucible" else "MTG.Design",
    )
    if config.rendering.engine == "crucible":
        config.crucible.repository_path = Path(st.text_input("Crucible repository", str(config.crucible.repository_path)))
        config.crucible.quality = st.selectbox("Crucible quality", ["high", "medium", "low"],
                                              index=["high", "medium", "low"].index(config.crucible.quality))
        st.caption("Local rendering uses prepared artwork directly. Source-art uploads and an MTG.Design session are optional.")
        with st.expander("Prepare Crucible"):
            st.code("python -m mtg_pipeline.setup_crucible --config " + str(config_path))
            st.caption("Run setup once, and again after updating the local Crucible clone. If Node.js is missing, add --download-node on Windows.")
            st.caption("If you change the repository path here, also save it as crucible.repository_path in your YAML before running setup.")
    if "mtg_design_session" not in st.session_state:
        st.session_state["mtg_design_session"] = os.environ.get("MTGDESIGN_LAVAREL_SESSION", "")
    laravel_session = st.text_input("MTG.Design Laravel Session", type="password", key="mtg_design_session", disabled=config.rendering.engine != "mtg_design",
        help="Paste the value of the `laravel_session` cookie from an authenticated MTG.Design browser session. It is kept only in memory.")
    if config.rendering.engine == "mtg_design":
        st.caption(f"Renderer: MTG.Design | Rate limit: 1 request / {config.mtg_design.request_delay_seconds:g} sec | Render cache: enabled")
    bucket, public_url, credentials_configured = r2_settings(config.r2)
    st.header("Cloudflare R2")
    st.write("Bucket:", bucket or "Not configured")
    st.write("Public URL:", public_url or "Not configured")
    st.write("Credentials configured:", "Yes" if credentials_configured else "No")
    unavailable = {}
    try:
        validate_public_url(public_url)
        if not bucket or not credentials_configured:
            raise ValueError("Configure the R2 bucket and credentials.")
    except ValueError as exc:
        unavailable = {"artwork_r2_upload": str(exc), "rendered_card_r2_upload": str(exc)}
    runner = build_pipeline(config)
    mandatory = {step.id for step in runner.steps if step.mandatory}
    opt_in_steps = {"print", "notes", "draftmancer", "cubecobra", "cockatrice", "rendered_card_r2_upload"}
    defaults = {step.id for step in runner.steps if step.poc_available and step.id not in opt_in_steps}
    if config.rendering.engine == "crucible":
        defaults.discard("artwork_r2_upload")
    selected = set(st.session_state.get("selected_steps", defaults))
    if st.session_state.get("previous_rendering_engine") != config.rendering.engine:
        if config.rendering.engine == "mtg_design":
            selected.add("artwork_r2_upload")
        else:
            selected.discard("artwork_r2_upload")
        st.session_state["step_artwork_r2_upload"] = "artwork_r2_upload" in selected
        st.session_state["previous_rendering_engine"] = config.rendering.engine

    st.header("Print options")
    config.print.bleed_enabled = st.checkbox(
        "Add 2 mm black bleed", value=config.print.bleed_enabled,
        help="Add black bleed around printable cards and fill their rounded-corner whites. Card trim size stays unchanged. Applies only to the print PDF.",
    )
    if config.print.bleed_enabled:
        config.print.bleed_mm = 2

    st.header("Pipeline selection")
    for step in runner.steps:
        availability = runner.availability(selected | mandatory, unavailable)
        available, reason = availability[step.id]
        disabled = step.mandatory or not available
        value = step.id in selected or step.mandatory
        if not available:
            st.session_state[f"step_{step.id}"] = False
            value = False
        checkbox_options = {"disabled": disabled, "help": step.description, "key": f"step_{step.id}"}
        if f"step_{step.id}" not in st.session_state:
            checkbox_options["value"] = value
        checked = st.checkbox(step.display_name, **checkbox_options)
        if checked:
            selected.add(step.id)
        else:
            selected.discard(step.id)
        if reason:
            st.caption(reason)
    st.session_state["selected_steps"] = selected

    st.header("Run")
    log_handler = StreamlitLogHandler()
    log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger = logging.getLogger("mtg_pipeline")
    logger.setLevel(logging.INFO)
    logger.addHandler(log_handler)

    needs_pack_layout = bool(selected & {"draftmancer", "mana_curve"})
    if needs_pack_layout and pack_errors:
        st.error("Fix the pack layout for Draftmancer / mana-curve analysis before running: " + " ".join(pack_errors))
    if st.button("Run selected steps", type="primary", disabled=needs_pack_layout and bool(pack_errors)):
        with st.status("Running pipeline", expanded=True) as status:
            progress_details = st.empty()
            activity: list[str] = []

            def show_progress(message: str) -> None:
                activity.append(message)
                status.update(label=message)
                progress_details.code("\n".join(activity[-12:]))

            context = PipelineContext(config=config, progress_callback=show_progress,
                                      mtg_design_session=laravel_session)
            result = runner.run(context, selected | mandatory)
            context.report_progress("Finalizing release manifest")
            manifest = finalize_manifest(context, selected | mandatory, result)
            if manifest:
                st.write(f"Manifest: {manifest}")
            failed = [step for step in result.steps.values() if step.status == "failed"]
            label = "Pipeline failed" if failed else ("Pipeline completed with warnings" if result.warnings else "Pipeline completed")
            status.update(label=label, state="error" if failed else "complete")
        _show_results(context, result, log_handler.buffer.getvalue())
    logger.removeHandler(log_handler)


def _edit_pack_layout(config, config_path: Path) -> list[str]:
    st.header("Draftmancer pack layout")
    if st.session_state.get("draft_editor_config_path") != str(config_path.resolve()):
        for key in list(st.session_state):
            if key.startswith("draft_editor_"):
                del st.session_state[key]
        st.session_state["draft_editor_config_path"] = str(config_path.resolve())
    settings = deepcopy(st.session_state.get("draft_editor_settings", config.draft.booster_layout))
    layouts = settings.get("layouts", {})
    sheet_names = [*RARITY_SHEETS.values(), "RareMythic"]
    simple = isinstance(layouts, dict) and len(layouts) == 1
    layout = next(iter(layouts.values())) if simple else None
    simple = (simple and isinstance(layout, dict) and isinstance(layout.get("slots"), dict)
              and all(sheet in sheet_names and type(count) is int and count >= 0
                      for sheet, count in layout["slots"].items())
              and type(layout.get("weight")) is int and layout["weight"] > 0)
    if not simple:
        st.session_state["draft_editor_mode"] = "Advanced JSON"
    mode = st.radio("Pack layout editor", ["Card counts", "Advanced JSON"],
                    key="draft_editor_mode")
    if mode != st.session_state.get("draft_editor_previous_mode"):
        for key in list(st.session_state):
            if key.startswith("draft_editor_count_") or key in {"draft_editor_json", "draft_editor_replacement", "draft_editor_refill"}:
                del st.session_state[key]
        st.session_state["draft_editor_previous_mode"] = mode
    if mode == "Card counts":
        columns = st.columns(3)
        slots = {}
        for index, sheet in enumerate(sheet_names):
            count = columns[index % 3].number_input(
                f"{sheet} cards per pack", min_value=0, step=1,
                value=layout["slots"].get(sheet, 0), key=f"draft_editor_count_{sheet}",
            )
            if count:
                slots[sheet] = int(count)
        layout["slots"] = slots
        st.write("Cards per pack:", sum(slots.values()))
        settings["withReplacement"] = st.checkbox(
            "Allow cards to appear in multiple packs", value=settings.get("withReplacement", False),
            key="draft_editor_replacement",
            help="Treat card quantities as unlimited. Useful when drafting a set rather than a singleton cube.",
        )
        settings["refillWhenEmpty"] = st.checkbox(
            "Refill sheets when exhausted", value=settings.get("refillWhenEmpty", False),
            key="draft_editor_refill",
        )
        st.caption("Set unused slots to 0. RareMythic combines rares and mythics with equal weight per card.")
    else:
        text = st.text_area("Draftmancer settings JSON", value=json.dumps(settings, indent=2),
                            height=300, key="draft_editor_json")
        try:
            settings = json.loads(text)
            if not isinstance(settings, dict):
                raise ValueError("Settings must be a JSON object.")
        except (ValueError, TypeError) as exc:
            message = f"Invalid pack settings JSON: {exc}"
            st.warning(message)
            return [message]
    available = None
    validation = st.session_state.get("loaded_validation")
    if validation:
        rarities = validation.get("rarity_counts", {})
        available = {sheet: rarities.get(rarity, 0) for rarity, sheet in RARITY_SHEETS.items()}
        available["RareMythic"] = available["Rare"] + available["Mythic"]
        st.write("Available cards by sheet", available)
    errors = pack_layout_errors(settings, available)
    for message in errors:
        st.warning(message)
    if not settings.get("withReplacement") and not settings.get("refillWhenEmpty"):
        st.caption("Each listed card can be used once across the draft. Enable reuse or refilling if you need more packs than the card pool supports.")
    config.draft.booster_layout = settings
    st.session_state["draft_editor_settings"] = deepcopy(settings)
    st.caption("Changes apply to this run. Download the pack settings and copy their draft section into your YAML configuration to keep them.")
    if not errors:
        st.download_button("Download pack configuration", yaml.safe_dump({"draft": {"booster_layout": settings}}, sort_keys=False),
                           file_name="draft-config.yaml", mime="text/yaml")
    return errors


def _show_results(context, result, logs: str) -> None:
    st.header("Validation summary")
    if context.validation_result:
        st.metric("Cards", context.validation_result.card_count)
        st.write("Rarity counts", context.validation_result.rarity_counts)
        if context.validation_result.errors:
            st.error("\n".join(issue.message for issue in context.validation_result.errors))
        else:
            st.success("No validation errors")
        if context.validation_result.warnings:
            st.warning("\n".join(issue.message for issue in context.validation_result.warnings))
        else:
            st.info("No validation warnings")

    st.header("Results")
    if result.warnings:
        st.warning("\n\n".join(result.warnings))
    if context.release_dir:
        st.write("Release directory", str(context.release_dir))
    st.write("Generated files")
    for path in result.output_files:
        st.code(str(path))
    curves = [path for path in result.output_files if path.suffix == ".png" and "mana-curves-" in path.stem]
    if curves:
        st.subheader("Mana curves (ASFAN)")
        tabs = st.tabs([path.stem.rsplit("-", 1)[-1] for path in curves])
        for tab, path in zip(tabs, curves):
            with tab:
                st.image(str(path))
    st.write("Step results")
    st.json({step_id: step.model_dump() for step_id, step in result.steps.items()})
    st.write("Logs")
    st.code(logs or "No logs")
    st.write("Manual next steps")
    st.write("- Upload rendered card images to your chosen public host if Draftmancer/Cockatrice image URLs point to placeholders.")
    st.write("- Import the Draftmancer custom set file manually.")
    st.write("- Validate the CubeCobra CSV through CubeCobra's UI.")
    st.write("- Copy the Cockatrice XML into Cockatrice's custom sets folder.")


def _running_under_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except Exception:
        return False
    return get_script_run_ctx() is not None


if __name__ == "__main__":
    if _running_under_streamlit():
        main()
    else:
        subprocess.run([sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve())], check=False)
