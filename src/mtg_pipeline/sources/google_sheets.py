from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from mtg_pipeline.config import SourceConfig
from mtg_pipeline.config import ColumnMapping
from mtg_pipeline.models import SourceData
from mtg_pipeline.sources.sheet_history import normalize_spreadsheet_id


@dataclass(frozen=True)
class TokenStatus:
    state: str
    message: str

    @property
    def can_authenticate(self) -> bool:
        return self.state != "valid"


@dataclass
class OAuthSession:
    auth_url: str
    port: int
    state: str
    flow: Any = field(repr=False)
    server: socketserver.TCPServer = field(repr=False)
    thread: threading.Thread = field(repr=False)
    event: threading.Event = field(repr=False)
    callback_url: str | None = None
    error: str | None = None
    state_mismatch: bool = False
    finished: bool = False

    def close(self) -> None:
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            return
        if self.thread.is_alive():
            self.thread.join(timeout=1)


class GoogleSheetsSource:
    def __init__(self, config: SourceConfig, progress: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.progress = progress

    def token_exists(self) -> bool:
        return self.config.token_path.exists()

    def token_has_required_scopes(self) -> bool:
        status = self.token_status()
        if status.state == "valid":
            return True
        if status.state != "expired":
            return False
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(self.config.token_path), self.config.scopes)
        except Exception:
            return False
        return set(self.config.scopes).issubset(set(creds.scopes or []))

    def token_status(self) -> TokenStatus:
        if not self.config.token_path.exists():
            return TokenStatus("missing", "No token.json found.")
        saved_scopes = _token_file_scopes(self.config.token_path)
        if saved_scopes and not set(self.config.scopes).issubset(saved_scopes):
            return TokenStatus("missing_scope", "token.json exists but does not include the current required Google Sheets scope.")
        try:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_file(str(self.config.token_path), self.config.scopes)
        except Exception as exc:
            return TokenStatus("invalid", f"token.json exists but could not be read: {exc}")
        if not saved_scopes and not set(self.config.scopes).issubset(set(creds.scopes or [])):
            return TokenStatus("missing_scope", "token.json exists but does not include the current required Google Sheets scope.")
        if creds.expired:
            return TokenStatus("expired", "token.json is expired and should be recreated.")
        if creds.valid:
            return TokenStatus("valid", "Using local token.json.")
        return TokenStatus("invalid", "token.json exists but is not valid.")

    def delete_token(self) -> None:
        if self.config.token_path.exists():
            self.config.token_path.unlink()

    def authenticate(self) -> None:
        creds = self._load_credentials()
        self._build_service(creds)

    def begin_authentication(self, reset_token: bool = False) -> OAuthSession:
        allow_localhost_oauth_transport()
        InstalledAppFlow = _installed_app_flow_class()
        if not self.config.credentials_path.exists():
            raise FileNotFoundError(f"Google OAuth credentials file not found: {self.config.credentials_path}")
        if reset_token:
            self.delete_token()

        flow = InstalledAppFlow.from_client_secrets_file(str(self.config.credentials_path), self.config.scopes)
        event = threading.Event()
        handler = _make_callback_handler(event)
        server = socketserver.TCPServer(("localhost", 0), handler)
        port = int(server.server_address[1])
        flow.redirect_uri = f"http://localhost:{port}/"
        auth_url, state = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
        session = OAuthSession(
            auth_url=auth_url,
            port=port,
            state=state,
            flow=flow,
            server=server,
            thread=threading.Thread(target=server.serve_forever, daemon=True),
            event=event,
        )
        server.oauth_session = session  # type: ignore[attr-defined]
        session.thread.start()
        return session

    def finish_authentication(self, session: OAuthSession) -> bool:
        if session.finished:
            return True
        if not session.event.is_set():
            return False
        try:
            if session.error:
                raise RuntimeError(f"Google OAuth failed: {session.error}")
            if not session.callback_url:
                return False
            params = _callback_params(session.callback_url)
            code = params.get("code")
            if not code:
                raise RuntimeError("Google OAuth callback did not include an authorization code. Start a fresh authentication attempt.")
            callback_state = params.get("state")
            session.state_mismatch = callback_state != session.state
            if session.state_mismatch:
                raise RuntimeError("Google OAuth state mismatch. Start a fresh authentication attempt and use the newest authorization link.")
            allow_localhost_oauth_transport()
            session.flow.fetch_token(authorization_response=session.callback_url)
            creds = session.flow.credentials
            self.config.token_path.write_text(creds.to_json(), encoding="utf-8")
            session.finished = True
            return True
        finally:
            session.close()

    def load(self) -> SourceData:
        creds = self._load_credentials()
        service = self._build_service(creds)
        spreadsheet_id = normalize_spreadsheet_id(self.config.spreadsheet_id)
        if not spreadsheet_id:
            raise ValueError("Google Sheets source requires source.spreadsheet_id in config or UI.")
        set_values = self._read_values(service, spreadsheet_id, self.config.set_worksheet)
        card_values = self._read_values(service, spreadsheet_id, self.config.cards_worksheet)
        return SourceData(
            set_metadata=_parse_set_values(set_values),
            card_rows=_parse_table_values(card_values),
        )

    def spreadsheet_title(self, spreadsheet_id: str | None = None) -> str:
        creds = self._load_credentials()
        service = self._build_service(creds)
        target_id = normalize_spreadsheet_id(spreadsheet_id or self.config.spreadsheet_id)
        if not target_id:
            return ""
        response = self._execute_read(service.spreadsheets().get(spreadsheetId=target_id, fields="properties/title"))
        return str(response.get("properties", {}).get("title", "")).strip()

    def update_generated_artwork_fields(self, worksheet: str, mapping: ColumnMapping, row_number: int, artwork_path: str, artist_credit: str) -> None:
        creds = self._load_credentials()
        service = self._build_service(creds)
        spreadsheet_id = normalize_spreadsheet_id(self.config.spreadsheet_id)
        headers = self._read_values(service, spreadsheet_id, f"{worksheet}!1:1")
        if not headers:
            raise RuntimeError(f"Cannot update generated artwork fields: worksheet '{worksheet}' has no header row.")
        header_to_column = {str(header).strip(): index + 1 for index, header in enumerate(headers[0])}
        missing = [column for column in (mapping.artwork_url, mapping.artist) if column not in header_to_column]
        if missing:
            raise RuntimeError(f"Cannot update generated artwork fields: missing column(s) in '{worksheet}': {', '.join(missing)}.")
        data = [
            {"range": f"{worksheet}!{_column_letter(header_to_column[mapping.artwork_url])}{row_number}", "values": [[artwork_path]]},
            {"range": f"{worksheet}!{_column_letter(header_to_column[mapping.artist])}{row_number}", "values": [[artist_credit]]},
        ]
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    def _load_credentials(self) -> Any:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise RuntimeError("Google dependencies are not installed. Run: pip install -e .") from exc

        creds = None
        token_path = self.config.token_path
        credentials_path = self.config.credentials_path
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.config.scopes)
            if not set(self.config.scopes).issubset(set(creds.scopes or [])):
                raise RuntimeError(
                    "token.json exists but does not include Google Sheets write scope. "
                    "Run: python -m mtg_pipeline.auth_google --config config.example.yaml --no-browser --reset-token"
                )
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                if _is_invalid_scope_refresh_error(exc):
                    raise RuntimeError(
                        "Google rejected the saved token refresh with invalid_scope. "
                        "The local token.json is expired and must be recreated. "
                        "Run: python -m mtg_pipeline.auth_google --config config.example.yaml --no-browser --reset-token"
                    ) from exc
                raise
        if not creds or not creds.valid:
            if not credentials_path.exists():
                raise FileNotFoundError(f"Google OAuth credentials file not found: {credentials_path}")
            InstalledAppFlow = _installed_app_flow_class()
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), self.config.scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _build_service(self, creds: Any) -> Any:
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("google-api-python-client is not installed. Run: pip install -e .") from exc
        return build("sheets", "v4", credentials=creds)

    def _read_values(self, service: Any, spreadsheet_id: str, worksheet: str) -> list[list[object]]:
        if self.progress:
            self.progress(f"Loading Google worksheet: {worksheet}")
        response = self._execute_read(service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=worksheet))
        return response.get("values", [])

    def _execute_read(self, request: Any) -> dict:
        from googleapiclient.errors import HttpError

        for attempt in range(3):
            try:
                return request.execute()
            except HttpError as exc:
                if exc.resp.status not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                message = f"Google Sheets returned HTTP {exc.resp.status}; retry {attempt + 1}/2 in 2 seconds."
                logging.getLogger("mtg_pipeline").warning("%s", message)
                if self.progress:
                    self.progress(message)
                time.sleep(2)
        raise AssertionError("Google Sheets retry loop exhausted unexpectedly.")


def _parse_set_values(values: list[list[object]]) -> dict[str, object]:
    if not values:
        return {}
    first = [str(cell).strip() for cell in values[0]]
    if len(first) >= 2 and first[0].lower() in {"key", "field", "name"} and first[1].lower() in {"value", "val"}:
        return {str(row[0]).strip(): row[1] if len(row) > 1 else "" for row in values[1:] if row}
    headers = [str(cell).strip() for cell in values[0]]
    row = values[1] if len(values) > 1 else []
    return {header: row[index] if index < len(row) else "" for index, header in enumerate(headers)}


def _parse_table_values(values: list[list[object]]) -> list[dict[str, object]]:
    if not values:
        return []
    headers = [str(cell).strip() for cell in values[0]]
    rows: list[dict[str, object]] = []
    for row_number, raw in enumerate(values[1:], start=2):
        row = {header: raw[index] if index < len(raw) else "" for index, header in enumerate(headers)}
        row["__row_number"] = row_number
        rows.append(row)
    return rows


def _installed_app_flow_class() -> Any:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError("google-auth-oauthlib is not installed. Run: pip install -e .") from exc
    return InstalledAppFlow


def allow_localhost_oauth_transport() -> None:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


def _is_invalid_scope_refresh_error(exc: Exception) -> bool:
    return "invalid_scope" in str(exc)


def _token_file_scopes(token_path: object) -> set[str]:
    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return set()
    value = data.get("granted_scopes") or data.get("scopes")
    if isinstance(value, str):
        return {scope for scope in value.replace(",", " ").split() if scope}
    if isinstance(value, list):
        return {str(scope).strip() for scope in value if str(scope).strip()}
    return set()


def _callback_state(callback_url: str) -> str | None:
    return parse_qs(urlparse(callback_url).query).get("state", [None])[0]


def _callback_params(callback_url: str) -> dict[str, str]:
    parsed = parse_qs(urlparse(callback_url).query)
    return {key: values[0] for key, values in parsed.items() if values}


def _is_oauth_callback_path(path: str) -> bool:
    params = parse_qs(urlparse(path).query)
    return "code" in params or "error" in params


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _make_callback_handler(event: threading.Event) -> type[http.server.BaseHTTPRequestHandler]:
    class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            session: OAuthSession = self.server.oauth_session  # type: ignore[attr-defined]
            if not _is_oauth_callback_path(self.path):
                self.send_response(204)
                self.end_headers()
                return
            if "error" in query:
                description = query.get("error_description", query["error"])[0]
                session.error = f"{query['error'][0]}: {description}"
            else:
                host = self.headers.get("Host", f"localhost:{session.port}")
                session.callback_url = f"http://{host}{self.path}"
            event.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Google authentication received</h1>"
                b"<p>You can return to the Streamlit release pipeline.</p></body></html>"
            )

        def log_message(self, format: str, *args: object) -> None:
            return

    return OAuthCallbackHandler
