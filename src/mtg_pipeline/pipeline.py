from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from mtg_pipeline.config import AppConfig
from mtg_pipeline.models import CardDefinition, SetDefinition, SourceData
from mtg_pipeline.validation import ValidationResult

LOGGER = logging.getLogger("mtg_pipeline")


@dataclass
class PipelineContext:
    config: AppConfig
    source_data: SourceData | None = None
    set_definition: SetDefinition | None = None
    cards: list[CardDefinition] = field(default_factory=list)
    validation_result: ValidationResult | None = None
    release_dir: Path | None = None
    output_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    project_root: Path = field(default_factory=Path.cwd)
    progress_callback: Callable[[str], None] | None = None

    def report_progress(self, message: str) -> None:
        LOGGER.info("%s", message)
        if self.progress_callback:
            self.progress_callback(message)


class StepResult(BaseModel):
    id: str
    status: str
    message: str = ""
    output_files: list[str] = Field(default_factory=list)


class PipelineRunResult(BaseModel):
    steps: dict[str, StepResult] = Field(default_factory=dict)
    output_files: list[Path] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class PipelineStep:
    id: str
    display_name: str
    description: str
    dependencies: tuple[str, ...]
    execute: Callable[[PipelineContext], list[Path]]
    mandatory: bool = False
    poc_available: bool = True


class PipelineRunner:
    def __init__(self, steps: list[PipelineStep]) -> None:
        self.steps = steps
        self.by_id = {step.id: step for step in steps}

    def availability(self, selected: set[str]) -> dict[str, tuple[bool, str]]:
        states: dict[str, tuple[bool, str]] = {}
        for step in self.steps:
            if not step.poc_available:
                states[step.id] = (False, "Future/full-pipeline step is not implemented in the PoC.")
                continue
            missing = [dep for dep in step.dependencies if dep not in selected]
            if missing:
                names = ", ".join(self.by_id[dep].display_name for dep in missing)
                states[step.id] = (False, f"Requires selected step: {names}.")
            else:
                states[step.id] = (True, "")
        return states

    def run(self, context: PipelineContext, selected: set[str]) -> PipelineRunResult:
        result = PipelineRunResult()
        selected = selected | {step.id for step in self.steps if step.mandatory}
        availability = self.availability(selected)
        total = sum(step.id in selected for step in self.steps)
        current = 0
        for step in self.steps:
            if step.id not in selected:
                result.steps[step.id] = StepResult(id=step.id, status="skipped", message="Not selected.")
                continue
            current += 1
            available, reason = availability[step.id]
            failed_deps = [dep for dep in step.dependencies if result.steps.get(dep) and result.steps[dep].status != "success"]
            if not available or failed_deps:
                message = reason or f"Skipped because dependency failed: {', '.join(failed_deps)}."
                result.steps[step.id] = StepResult(id=step.id, status="skipped", message=message)
                continue
            context.report_progress(f"Step {current}/{total}: {step.display_name}")
            warning_count = len(context.warnings)
            try:
                outputs = step.execute(context)
                context.output_files.extend(outputs)
                result.output_files.extend(outputs)
                result.steps[step.id] = StepResult(id=step.id, status="success", output_files=[str(path) for path in outputs])
            except Exception as exc:
                LOGGER.exception("Step failed: %s", step.display_name)
                result.steps[step.id] = StepResult(id=step.id, status="failed", message=str(exc))
            finally:
                for warning in context.warnings[warning_count:]:
                    LOGGER.warning("%s", warning)
        result.warnings.extend(context.warnings)
        return result
