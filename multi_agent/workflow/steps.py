"""Step type metadata, default workflow definition, and mandatory step constraints."""

from __future__ import annotations

from multi_agent.workflow.models import Step, StepStatus, StepType


# ---------------------------------------------------------------------------
# Per-step timeout in seconds
# ---------------------------------------------------------------------------

STEP_TIMEOUTS: dict[StepType, int] = {
    StepType.context_gathering: 900,
    StepType.planning: 600,
    StepType.architecture: 600,
    StepType.test_architecture: 600,
    StepType.coding: 1800,
    StepType.linting: 300,
    StepType.initial_testing: 1200,
    StepType.review: 600,
    StepType.prune_tests: 600,
    StepType.final_review: 900,
}

# ---------------------------------------------------------------------------
# Which steps are allowed to request workflow edits
# ---------------------------------------------------------------------------

STEP_ALLOWS_EDITING: dict[StepType, bool] = {
    StepType.context_gathering: False,
    StepType.planning: True,
    StepType.architecture: True,
    StepType.test_architecture: True,
    StepType.coding: True,
    StepType.linting: False,
    StepType.initial_testing: True,
    StepType.review: True,
    StepType.prune_tests: False,
    StepType.final_review: True,
}

# ---------------------------------------------------------------------------
# Steps that cannot be removed or skipped
# ---------------------------------------------------------------------------

MANDATORY_STEPS: set[StepType] = {StepType.linting, StepType.final_review}

# ---------------------------------------------------------------------------
# Workflow limits
# ---------------------------------------------------------------------------

MAX_STEPS_PER_WORKFLOW: int = 30
MAX_RESTARTS_PER_STEP: int = 3

# ---------------------------------------------------------------------------
# Default workflow
# ---------------------------------------------------------------------------

_DEFAULT_STEPS: list[tuple[str, StepType, str]] = [
    ('step-001', StepType.context_gathering, 'Explore codebase, DB schema, docs, and related code'),
    ('step-002', StepType.planning, 'Produce implementation plan based on gathered context'),
    ('step-003', StepType.architecture, 'Design code structure and identify files to modify'),
    ('step-004', StepType.test_architecture, 'Design test strategy and identify test files'),
    ('step-005', StepType.coding, 'Implement the changes'),
    ('step-006', StepType.linting, 'Run formatters and lint checks'),
    ('step-007', StepType.initial_testing, 'Run tests and identify failures'),
    ('step-008', StepType.review, 'Self-review against acceptance criteria'),
    ('step-009', StepType.prune_tests, 'Remove redundant tests'),
    ('step-010', StepType.final_review, 'Final verification and commit'),
]


def create_default_workflow() -> list[Step]:
    """Return the 10-step default workflow with all steps in pending state."""
    return [
        Step(
            id=step_id,
            type=step_type,
            status=StepStatus.pending,
            description=description,
            started_at=None,
            completed_at=None,
            notes=None,
            restart_count=0,
            cost_usd=None,
            input_tokens=None,
            output_tokens=None,
        )
        for step_id, step_type, description in _DEFAULT_STEPS
    ]
