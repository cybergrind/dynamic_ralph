"""Workflow editing: parse, validate, and apply edit operations."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from multi_agent.workflow.models import (
    AddAfterEdit,
    EditDescriptionEdit,
    EditOperation,
    ReorderEdit,
    RestartEdit,
    SkipEdit,
    SplitEdit,
    Step,
    StepStatus,
    StepType,
    StoryWorkflow,
)


if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

EDITS_DIR = Path('workflow_edits')


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def parse_edit_file(story_id: str, shared_dir: Path) -> list[EditOperation] | None:
    """Read and parse a workflow edit request file.

    Returns None if no edit file exists. Raises ValueError on invalid JSON.
    """
    edit_path = shared_dir / EDITS_DIR / f'{story_id}.json'
    if not edit_path.exists():
        return None

    with open(edit_path) as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raw = [raw]

    operations: list[EditOperation] = []
    for item in raw:
        op_type = item.get('operation')
        if op_type == 'add_after':
            operations.append(AddAfterEdit.model_validate(item))
        elif op_type == 'split':
            operations.append(SplitEdit.model_validate(item))
        elif op_type == 'skip':
            operations.append(SkipEdit.model_validate(item))
        elif op_type == 'reorder':
            operations.append(ReorderEdit.model_validate(item))
        elif op_type == 'edit_description':
            operations.append(EditDescriptionEdit.model_validate(item))
        elif op_type == 'restart':
            operations.append(RestartEdit.model_validate(item))
        else:
            raise ValueError(f'Unknown edit operation: {op_type}')

    return operations


def discard_edit_file(story_id: str, shared_dir: Path) -> None:
    """Move edit file to failed/ subdirectory for debugging."""
    edit_path = shared_dir / EDITS_DIR / f'{story_id}.json'
    if not edit_path.exists():
        return
    failed_dir = shared_dir / EDITS_DIR / 'failed'
    failed_dir.mkdir(parents=True, exist_ok=True)
    edit_path.rename(failed_dir / f'{story_id}.json')


def remove_edit_file(story_id: str, shared_dir: Path) -> None:
    """Delete the edit file after successful application."""
    edit_path = shared_dir / EDITS_DIR / f'{story_id}.json'
    if edit_path.exists():
        edit_path.unlink()


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class EditValidationError(Exception):
    """Raised when workflow edits fail validation."""


def validate_edits(story: StoryWorkflow, operations: list[EditOperation]) -> None:
    """Validate all edit operations against guardrails.

    Raises EditValidationError if any operation fails. Validation is atomic —
    all operations are checked before any are applied.
    """
    from multi_agent.workflow.steps import MANDATORY_STEPS, MAX_RESTARTS_PER_STEP, MAX_STEPS_PER_WORKFLOW

    errors: list[str] = []

    # Simulate step count to check MAX_STEPS_PER_WORKFLOW
    simulated_count = len(story.steps)

    for op in operations:
        if isinstance(op, AddAfterEdit):
            # Target step must exist (can be any status — we're adding after it)
            target = story.find_step(op.target_step_id)
            if target is None:
                errors.append(f"add_after: target step '{op.target_step_id}' not found")

            # Cannot add after final_review
            if target and target.type == StepType.final_review:
                errors.append('add_after: cannot add steps after final_review')

            simulated_count += len(op.new_steps)

        elif isinstance(op, SplitEdit):
            target = story.find_step(op.target_step_id)
            if target is None:
                errors.append(f"split: target step '{op.target_step_id}' not found")
            elif target.status != StepStatus.pending:
                errors.append(f"split: can only split pending steps, '{op.target_step_id}' is {target.status}")
            elif target.type in MANDATORY_STEPS:
                errors.append(f"split: cannot split mandatory step type '{target.type}'")

            # Split replaces 1 step with N, net change is N-1
            simulated_count += len(op.replacement_steps) - 1

        elif isinstance(op, SkipEdit):
            target = story.find_step(op.target_step_id)
            if target is None:
                errors.append(f"skip: target step '{op.target_step_id}' not found")
            elif target.status != StepStatus.pending:
                errors.append(f"skip: can only skip pending steps, '{op.target_step_id}' is {target.status}")
            elif target.type in MANDATORY_STEPS:
                errors.append(f"skip: cannot skip mandatory step type '{target.type}'")

        elif isinstance(op, ReorderEdit):
            pending_ids = [s.id for s in story.steps if s.status == StepStatus.pending]
            if set(op.new_order) != set(pending_ids):
                errors.append(
                    f'reorder: new_order must contain exactly all pending step IDs. '
                    f'Expected: {pending_ids}, got: {op.new_order}'
                )
            # final_review must be last in new_order
            final_review_steps = [
                s for s in story.steps if s.status == StepStatus.pending and s.type == StepType.final_review
            ]
            if final_review_steps and op.new_order and op.new_order[-1] != final_review_steps[0].id:
                errors.append('reorder: final_review must remain the last step')

        elif isinstance(op, EditDescriptionEdit):
            target = story.find_step(op.target_step_id)
            if target is None:
                errors.append(f"edit_description: target step '{op.target_step_id}' not found")
            elif target.status != StepStatus.pending:
                errors.append(
                    f"edit_description: can only edit pending steps, '{op.target_step_id}' is {target.status}"
                )

        elif isinstance(op, RestartEdit):
            target = story.find_step(op.target_step_id)
            if target is None:
                errors.append(f"restart: target step '{op.target_step_id}' not found")
            elif target.status != StepStatus.in_progress:
                errors.append(f"restart: can only restart in_progress steps, '{op.target_step_id}' is {target.status}")
            elif target.restart_count >= MAX_RESTARTS_PER_STEP:
                errors.append(f"restart: step '{op.target_step_id}' has reached max restarts ({MAX_RESTARTS_PER_STEP})")

    # Check total step count
    if simulated_count > MAX_STEPS_PER_WORKFLOW:
        errors.append(f'Total steps would be {simulated_count}, exceeding maximum of {MAX_STEPS_PER_WORKFLOW}')

    if errors:
        raise EditValidationError('; '.join(errors))


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_edits(story: StoryWorkflow, operations: list[EditOperation]) -> None:
    """Apply validated edit operations to a story workflow.

    Call validate_edits() first — this function assumes validation passed.
    """
    for op in operations:
        if isinstance(op, AddAfterEdit):
            _apply_add_after(story, op)
        elif isinstance(op, SplitEdit):
            _apply_split(story, op)
        elif isinstance(op, SkipEdit):
            _apply_skip(story, op)
        elif isinstance(op, ReorderEdit):
            _apply_reorder(story, op)
        elif isinstance(op, EditDescriptionEdit):
            _apply_edit_description(story, op)
        elif isinstance(op, RestartEdit):
            _apply_restart(story, op)


def _apply_add_after(story: StoryWorkflow, op: AddAfterEdit) -> None:
    """Insert new steps after the target step."""
    target_idx = next(i for i, s in enumerate(story.steps) if s.id == op.target_step_id)

    new_steps: list[Step] = []
    for spec in op.new_steps:
        new_steps.append(
            Step(
                id=story.next_step_id(),
                type=spec.type,
                description=spec.description,
            )
        )

    # Insert after target
    story.steps[target_idx + 1 : target_idx + 1] = new_steps


def _apply_split(story: StoryWorkflow, op: SplitEdit) -> None:
    """Replace a pending step with multiple steps."""
    target_idx = next(i for i, s in enumerate(story.steps) if s.id == op.target_step_id)

    new_steps: list[Step] = []
    for spec in op.replacement_steps:
        new_steps.append(
            Step(
                id=story.next_step_id(),
                type=spec.type,
                description=spec.description,
            )
        )

    story.steps[target_idx : target_idx + 1] = new_steps


def _apply_skip(story: StoryWorkflow, op: SkipEdit) -> None:
    """Mark a pending step as skipped."""
    step = story.find_step(op.target_step_id)
    if step:
        step.status = StepStatus.skipped
        step.skip_reason = op.reason


def _apply_reorder(story: StoryWorkflow, op: ReorderEdit) -> None:
    """Reorder pending steps according to new_order."""
    # Separate non-pending and pending steps
    non_pending = [s for s in story.steps if s.status != StepStatus.pending]
    pending_by_id = {s.id: s for s in story.steps if s.status == StepStatus.pending}

    # Build reordered pending list
    reordered_pending = [pending_by_id[sid] for sid in op.new_order]

    # Reconstruct: non-pending steps keep their position, pending steps go after
    story.steps = non_pending + reordered_pending


def _apply_edit_description(story: StoryWorkflow, op: EditDescriptionEdit) -> None:
    """Modify a pending step's description."""
    step = story.find_step(op.target_step_id)
    if step:
        step.description = op.new_description


def _apply_restart(story: StoryWorkflow, op: RestartEdit) -> None:
    """Edit description of in_progress step and reset to pending for re-execution."""
    step = story.find_step(op.target_step_id)
    if step:
        step.description = op.new_description
        step.status = StepStatus.pending
        step.restart_count += 1
        step.started_at = None
        step.completed_at = None
        step.notes = None
        step.error = None
        step.cost_usd = None
        step.input_tokens = None
        step.output_tokens = None
        step.log_file = None
