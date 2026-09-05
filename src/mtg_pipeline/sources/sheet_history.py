from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse


def load_sheet_history(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(sheet_id): str(name) for sheet_id, name in data.items()}


def save_sheet_history(path: Path, sheet_id: str, sheet_name: str) -> None:
    sheet_id = normalize_spreadsheet_id(sheet_id)
    sheet_name = sheet_name.strip()
    if not sheet_id:
        return
    history = load_sheet_history(path)
    history[sheet_id] = sheet_name or "unknown"
    path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_sheet_option(sheet_id: str, sheet_name: str) -> str:
    return f"{sheet_id} ({sheet_name})"


def parse_sheet_option(option: str) -> str:
    return option.split(" (", 1)[0].strip()


def sheet_options(history: dict[str, str]) -> list[str]:
    return [format_sheet_option(sheet_id, name) for sheet_id, name in sorted(history.items(), key=lambda item: item[1].casefold())]


def normalize_spreadsheet_id(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if "d" in parts:
            index = parts.index("d")
            if index + 1 < len(parts):
                return parts[index + 1]
        raise ValueError("Google Sheets URL does not contain a spreadsheet ID segment like /spreadsheets/d/<id>/edit.")
    if text.isdigit() and len(text) < 20:
        raise ValueError(f"'{text}' looks like a sheet tab gid, not a spreadsheet ID. Use the long ID between /d/ and /edit in the Google Sheets URL.")
    return text
