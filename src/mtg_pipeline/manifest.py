from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from mtg_pipeline.models import CardDefinition, SetDefinition
from mtg_pipeline.pipeline import PipelineRunResult


def write_manifest(set_def: SetDefinition, cards: list[CardDefinition], selected_steps: set[str], run_result: PipelineRunResult, release_dir: Path) -> Path:
    path = release_dir / "manifest.json"
    rarity_counts = Counter(card.rarity for card in cards)
    payload = {
        "set_id": set_def.set_id,
        "set_name": set_def.set_name,
        "set_code": set_def.set_code,
        "version": set_def.version,
        "build_timestamp": datetime.now(timezone.utc).isoformat(),
        "card_count": len(cards),
        "counts_by_rarity": dict(rarity_counts),
        "selected_pipeline_steps": sorted(selected_steps),
        "steps": {step_id: result.model_dump(mode="json") for step_id, result in run_result.steps.items()},
        "warnings": run_result.warnings,
        "generated_artifact_paths": [str(path) for path in run_result.output_files],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    run_result.output_files.append(path)
    return path
