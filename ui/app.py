from __future__ import annotations

import logging
import subprocess
import sys
from io import StringIO
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mtg_pipeline.config import load_config
from mtg_pipeline.validation import validate_source
from mtg_pipeline.pipeline import PipelineContext
from mtg_pipeline.sources.google_sheets import GoogleSheetsSource
from mtg_pipeline.sources.sheet_history import load_sheet_history, normalize_spreadsheet_id, parse_sheet_option, save_sheet_history, sheet_options
from mtg_pipeline.steps import build_pipeline, finalize_manifest

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

    runner = build_pipeline()
    mandatory = {step.id for step in runner.steps if step.mandatory}
    opt_in_steps = {"print", "draftmancer", "cubecobra", "cockatrice"}
    defaults = {step.id for step in runner.steps if step.poc_available and step.id not in opt_in_steps}
    selected = set(st.session_state.get("selected_steps", defaults))

    st.header("Pipeline selection")
    for step in runner.steps:
        availability = runner.availability(selected | mandatory)
        available, reason = availability[step.id]
        disabled = step.mandatory or not available
        value = step.id in selected or step.mandatory
        checked = st.checkbox(step.display_name, value=value, disabled=disabled, help=step.description, key=f"step_{step.id}")
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

    if st.button("Run selected steps", type="primary"):
        with st.status("Running pipeline", expanded=True) as status:
            progress_details = st.empty()
            activity: list[str] = []

            def show_progress(message: str) -> None:
                activity.append(message)
                status.update(label=message)
                progress_details.code("\n".join(activity[-12:]))

            context = PipelineContext(config=config, progress_callback=show_progress)
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
