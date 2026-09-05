from unittest.mock import Mock, call, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from mtg_pipeline.config import SourceConfig
from mtg_pipeline.sources.google_sheets import GoogleSheetsSource


def http_error(status):
    return HttpError(httplib2.Response({"status": str(status)}), b'{"error":{"message":"Test failure"}}')


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_sheet_read_retries_after_two_seconds(status):
    progress = []
    source = GoogleSheetsSource(SourceConfig(spreadsheet_id="test-sheet"), progress=progress.append)
    set_request = Mock()
    set_request.execute.side_effect = [http_error(status), {"values": [["key", "value"], ["set_name", "Test"]]}]
    cards_request = Mock()
    cards_request.execute.return_value = {"values": [["name"], ["Test Card"]]}
    service = Mock()
    service.spreadsheets.return_value.values.return_value.get.side_effect = [set_request, cards_request]
    with (
        patch.object(source, "_load_credentials"),
        patch.object(source, "_build_service", return_value=service),
        patch("mtg_pipeline.sources.google_sheets.time.sleep") as sleep,
    ):
        result = source.load()
    assert result.set_metadata["set_name"] == "Test"
    assert result.card_rows[0]["name"] == "Test Card"
    assert set_request.execute.call_count == 2
    cards_request.execute.assert_called_once()
    sleep.assert_called_once_with(2)
    assert any(f"HTTP {status}; retry 1/2 in 2 seconds" in message for message in progress)


def test_repeated_503_has_bounded_retries():
    source = GoogleSheetsSource(SourceConfig())
    request = Mock()
    request.execute.side_effect = http_error(503)
    with patch("mtg_pipeline.sources.google_sheets.time.sleep") as sleep:
        with pytest.raises(HttpError):
            source._execute_read(request)
    assert request.execute.call_count == 3
    assert sleep.call_args_list == [call(2), call(2)]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_sheet_error_does_not_retry(status):
    source = GoogleSheetsSource(SourceConfig())
    request = Mock()
    request.execute.side_effect = http_error(status)
    with patch("mtg_pipeline.sources.google_sheets.time.sleep") as sleep:
        with pytest.raises(HttpError):
            source._execute_read(request)
    request.execute.assert_called_once()
    sleep.assert_not_called()
