"""Opt-in billed check: RUN_GEMINI_LIVE_TEST=1 and GEMINI_API_KEY are both required."""
import os

import pytest
from PIL import Image

from mtg_pipeline.artwork.gemini_images import GeminiImageGenerator


@pytest.mark.skipif(os.environ.get("RUN_GEMINI_LIVE_TEST") != "1", reason="Paid Gemini API call requires explicit opt-in")
def test_gemini_live_image(tmp_path):
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY is required")
    output = GeminiImageGenerator(retry_count=0).generate(
        "An illustration of a small green seed creature in a peaceful forest clearing. No text.",
        tmp_path / "gemini-live.png",
    )
    with Image.open(output) as image:
        image.verify()
