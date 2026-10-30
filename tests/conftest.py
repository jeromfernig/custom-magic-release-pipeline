"""Block accidental paid SDK calls unless the dedicated live check is opted in."""
import os

import httpx
import pytest


@pytest.fixture(autouse=True)
def prevent_unmocked_sdk_requests(monkeypatch, request):
    if request.node.path.name == "test_gemini_live.py" and os.environ.get("RUN_GEMINI_LIVE_TEST") == "1":
        return

    def blocked(*args, **kwargs):
        pytest.fail("Unmocked SDK HTTP request blocked. Mock API calls or use the opt-in live smoke test.")

    monkeypatch.setattr(httpx.Client, "send", blocked)
