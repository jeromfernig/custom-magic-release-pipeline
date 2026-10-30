"""Explicit, single-image paid smoke test. Never runs as part of the pipeline/tests."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mtg_pipeline.artwork.generation import safe_error
from mtg_pipeline.artwork.gemini_images import GeminiImageGenerator
from mtg_pipeline.artwork.openai_images import OpenAIImageGenerator
from mtg_pipeline.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to one billable generation request.")
    parser.add_argument("--config", type=Path, default=Path("config.example.yaml"))
    parser.add_argument("--provider", choices=["openai", "gemini"], default="gemini")
    parser.add_argument("--model", help="Override the chosen provider's configured model.")
    parser.add_argument("--prompt-file", type=Path, required=True, help="UTF-8 file containing the exact existing art prompt.")
    parser.add_argument("--output", type=Path, default=Path("artwork/smoke-test.png"))
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to authorize a paid API request.")
    config = load_config(args.config).images
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generator = (
        GeminiImageGenerator(args.model or config.gemini_model, config.gemini_aspect_ratio,
                             config.gemini_image_size, retry_count=0,
                             timeout_seconds=config.generation_timeout_seconds)
        if args.provider == "gemini" else
        OpenAIImageGenerator(args.model or config.generation_model, config.generation_size, config.generation_quality)
    )
    try:
        path = generator.generate(args.prompt_file.read_text(encoding="utf-8-sig"), args.output)
    except Exception as exc:
        parser.exit(1, safe_error(f"Image smoke test failed: {exc}\n"))
    print(f"Saved image: {path}")


if __name__ == "__main__":
    main()
