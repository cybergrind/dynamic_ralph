"""Pydantic models for Dynamic Ralph workflow state."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StepType(StrEnum):
    context_gathering = 'context_gathering'
    planning = 'planning'
    architecture = 'architecture'
    test_architecture = 'test_architecture'
    coding = 'coding'
    linting = 'linting'
    initial_testing = 'initial_testing'
    review = 'review'
    prune_tests = 'prune_tests'
    final_review = 'final_review'


class StepStatus(StrEnum):
    pending = 'pending'
    in_progress = 'in_progress'
    completed = 'completed'
    skipped = 'skipped'
    failed = 'failed'
    cancelled = 'cancelled'


class StoryStatus(StrEnum):
    unclaimed = 'unclaimed'
    in_progress = 'in_progress'
    completed = 'completed'
    failed = 'failed'
    blocked = 'blocked'


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


class Step(BaseModel):
    id: str
    type: StepType
    status: StepStatus = StepStatus.pending
    description: str = ''
    started_at: str | None = None
    completed_at: str | None = None
    git_sha_at_start: str | None = None
    notes: str | None = None
    error: str | None = None
    skip_reason: str | None = None
    restart_count: int = 0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    log_file: str | None = None


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

HistoryAction = Literal[
    'step_started',
    'step_completed',
    'step_failed',
    'step_cancelled',
    'step_skipped',
    'workflow_edit',
    'story_claimed',
    'story_completed',
    'story_failed',
]


class HistoryEntry(BaseModel):
    timestamp: str
    action: HistoryAction
    agent_id: int | None = None
    step_id: str | None = None
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Story workflow
# ---------------------------------------------------------------------------


class StoryWorkflow(BaseModel):
    story_id: str
    title: str
    description: str = ''
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: StoryStatus = StoryStatus.unclaimed
    agent_id: int | None = None
    claimed_at: str | None = None
    completed_at: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)

    # Internal counter for generating new step IDs
    _next_step_counter: int = 11

    def model_post_init(self, __context: object) -> None:
        """Set _next_step_counter based on the highest existing step ID."""
        max_num = 10  # default workflow ends at step-010
        for step in self.steps:
            try:
                num = int(step.id.split('-')[1])
                if num > max_num:
                    max_num = num
            except (IndexError, ValueError):
                pass
        self._next_step_counter = max_num + 1

    def next_step_id(self) -> str:
        step_id = f'step-{self._next_step_counter:03d}'
        self._next_step_counter += 1
        return step_id

    def find_next_pending_step(self) -> Step | None:
        for step in self.steps:
            if step.status == StepStatus.pending:
                return step
        return None

    def find_step(self, step_id: str) -> Step | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None


# ---------------------------------------------------------------------------
# Workflow state file (top-level)
# ---------------------------------------------------------------------------


class WorkflowState(BaseModel):
    version: int = 1
    created_at: str = ''
    prd_file: str = ''
    stories: dict[str, StoryWorkflow] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Workflow edit operations
# ---------------------------------------------------------------------------


class AddAfterEdit(BaseModel):
    operation: Literal['add_after'] = 'add_after'
    target_step_id: str
    reason: str
    new_steps: list[NewStepSpec]


class NewStepSpec(BaseModel):
    type: StepType
    description: str


# Rebuild AddAfterEdit now that NewStepSpec is defined
AddAfterEdit.model_rebuild()


class SplitEdit(BaseModel):
    operation: Literal['split'] = 'split'
    target_step_id: str
    reason: str
    replacement_steps: list[NewStepSpec]


class SkipEdit(BaseModel):
    operation: Literal['skip'] = 'skip'
    target_step_id: str
    reason: str


class ReorderEdit(BaseModel):
    operation: Literal['reorder'] = 'reorder'
    reason: str
    new_order: list[str]


class EditDescriptionEdit(BaseModel):
    operation: Literal['edit_description'] = 'edit_description'
    target_step_id: str
    reason: str
    new_description: str


class RestartEdit(BaseModel):
    operation: Literal['restart'] = 'restart'
    target_step_id: str
    reason: str
    new_description: str


EditOperation = AddAfterEdit | SplitEdit | SkipEdit | ReorderEdit | EditDescriptionEdit | RestartEdit
