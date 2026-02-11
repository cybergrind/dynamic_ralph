"""Tests for multi_agent.workflow modules (models, steps, editing, state, scratch, prompts)."""

from __future__ import annotations

import io
import json

import pytest
from pydantic import ValidationError

from multi_agent.workflow.editing import (
    EditValidationError,
    apply_edits,
    discard_edit_file,
    parse_edit_file,
    remove_edit_file,
    validate_edits,
)
from multi_agent.workflow.executor import _tee_stderr
from multi_agent.workflow.models import (
    AddAfterEdit,
    EditDescriptionEdit,
    NewStepSpec,
    ReorderEdit,
    RestartEdit,
    SkipEdit,
    SplitEdit,
    Step,
    StepStatus,
    StepType,
    StoryStatus,
    StoryWorkflow,
    WorkflowState,
)
from multi_agent.workflow.prompts import (
    STEP_INSTRUCTIONS,
    _format_remaining_steps,
    compose_step_prompt,
)
from multi_agent.workflow.scratch import (
    append_global_scratch,
    append_story_scratch,
    cleanup_story_scratch,
    read_global_scratch,
    read_story_scratch,
    write_global_scratch,
    write_story_scratch,
)
from multi_agent.workflow.state import (
    find_assignable_story,
    initialize_state_from_prd,
    load_state,
    locked_state,
    save_state,
    validate_dependency_graph,
)
from multi_agent.workflow.steps import (
    MANDATORY_STEPS,
    MAX_RESTARTS_PER_STEP,
    STEP_ALLOWS_EDITING,
    STEP_TIMEOUTS,
    create_default_workflow,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_story(
    story_id: str = 'US-001',
    title: str = 'Test story',
    steps: list[Step] | None = None,
    depends_on: list[str] | None = None,
    status: StoryStatus = StoryStatus.unclaimed,
) -> StoryWorkflow:
    return StoryWorkflow(
        story_id=story_id,
        title=title,
        description='A test story',
        acceptance_criteria=['AC-1', 'AC-2'],
        status=status,
        depends_on=depends_on or [],
        steps=steps or create_default_workflow(),
    )


def _make_state(*stories: StoryWorkflow) -> WorkflowState:
    return WorkflowState(
        version=1,
        created_at='2025-01-01T00:00:00Z',
        prd_file='prd.json',
        stories={s.story_id: s for s in stories},
    )


# ===========================================================================
# models.py
# ===========================================================================


class TestStoryWorkflow:
    def test_next_step_id_starts_at_011(self):
        story = _make_story()
        assert story.next_step_id() == 'step-011'
        assert story.next_step_id() == 'step-012'

    def test_next_step_id_accounts_for_existing(self):
        steps = create_default_workflow()
        steps.append(Step(id='step-015', type=StepType.coding))
        story = _make_story(steps=steps)
        assert story.next_step_id() == 'step-016'

    def test_find_next_pending_step(self):
        story = _make_story()
        step = story.find_next_pending_step()
        assert step is not None
        assert step.id == 'step-001'

    def test_find_next_pending_skips_completed(self):
        story = _make_story()
        story.steps[0].status = StepStatus.completed
        story.steps[1].status = StepStatus.completed
        step = story.find_next_pending_step()
        assert step is not None
        assert step.id == 'step-003'

    def test_find_next_pending_returns_none_when_all_done(self):
        story = _make_story()
        for s in story.steps:
            s.status = StepStatus.completed
        assert story.find_next_pending_step() is None

    def test_find_step(self):
        story = _make_story()
        assert story.find_step('step-005') is not None
        assert story.find_step('step-999') is None


# ===========================================================================
# steps.py
# ===========================================================================


class TestSteps:
    def test_default_workflow_step_types(self):
        steps = create_default_workflow()
        types = [s.type for s in steps]
        assert types == [
            StepType.context_gathering,
            StepType.planning,
            StepType.architecture,
            StepType.test_architecture,
            StepType.coding,
            StepType.linting,
            StepType.initial_testing,
            StepType.review,
            StepType.prune_tests,
            StepType.final_review,
        ]

    def test_mandatory_steps(self):
        assert MANDATORY_STEPS == {StepType.linting, StepType.final_review}

    def test_step_timeouts_cover_all_types(self):
        for st in StepType:
            assert st in STEP_TIMEOUTS

    def test_step_editing_flags_cover_all_types(self):
        for st in StepType:
            assert st in STEP_ALLOWS_EDITING


# ===========================================================================
# state.py
# ===========================================================================


class TestState:
    def test_save_and_load_roundtrip(self, tmp_path):
        state = _make_state(_make_story())
        state_path = tmp_path / 'state.json'
        save_state(state, state_path)

        loaded = load_state(state_path)
        assert loaded.version == 1
        assert 'US-001' in loaded.stories
        assert loaded.stories['US-001'].title == 'Test story'

    def test_locked_state_context_manager(self, tmp_path):
        state = _make_state(_make_story())
        state_path = tmp_path / 'state.json'
        save_state(state, state_path)

        with locked_state(state_path) as s:
            s.stories['US-001'].status = StoryStatus.in_progress

        reloaded = load_state(state_path)
        assert reloaded.stories['US-001'].status == StoryStatus.in_progress

    def test_find_assignable_story_basic(self):
        s1 = _make_story('US-001', status=StoryStatus.unclaimed)
        state = _make_state(s1)
        result = find_assignable_story(state)
        assert result is not None
        assert result.story_id == 'US-001'

    def test_find_assignable_story_skips_in_progress(self):
        s1 = _make_story('US-001', status=StoryStatus.in_progress)
        state = _make_state(s1)
        assert find_assignable_story(state) is None

    def test_find_assignable_story_respects_dependencies(self):
        s1 = _make_story('US-001', status=StoryStatus.unclaimed)
        s2 = _make_story('US-002', status=StoryStatus.unclaimed, depends_on=['US-001'])
        state = _make_state(s1, s2)

        result = find_assignable_story(state)
        assert result.story_id == 'US-001'

    def test_find_assignable_story_unblocks_when_dep_completed(self):
        s1 = _make_story('US-001', status=StoryStatus.completed)
        s2 = _make_story('US-002', status=StoryStatus.unclaimed, depends_on=['US-001'])
        state = _make_state(s1, s2)

        result = find_assignable_story(state)
        assert result.story_id == 'US-002'


class TestDependencyValidation:
    def test_valid_graph(self):
        s1 = _make_story('US-001')
        s2 = _make_story('US-002', depends_on=['US-001'])
        state = _make_state(s1, s2)
        validate_dependency_graph(state)  # should not raise

    def test_circular_dependency(self):
        s1 = _make_story('US-001', depends_on=['US-002'])
        s2 = _make_story('US-002', depends_on=['US-001'])
        state = _make_state(s1, s2)
        with pytest.raises(ValueError, match='Circular dependency'):
            validate_dependency_graph(state)

    def test_missing_dependency(self):
        s1 = _make_story('US-001', depends_on=['US-999'])
        state = _make_state(s1)
        with pytest.raises(ValueError, match='does not exist'):
            validate_dependency_graph(state)


class TestInitializeStateFromPrd:
    def test_flat_array_format(self, tmp_path):
        prd_data = [
            {
                'id': 'US-001',
                'title': 'Story 1',
                'description': 'Desc 1',
                'acceptanceCriteria': ['AC-1'],
            },
            {
                'id': 'US-002',
                'title': 'Story 2',
                'description': 'Desc 2',
            },
        ]
        prd_path = tmp_path / 'prd.json'
        prd_path.write_text(json.dumps(prd_data))
        state_path = tmp_path / 'state.json'

        state = initialize_state_from_prd(prd_path, state_path)
        assert len(state.stories) == 2
        assert state.stories['US-001'].acceptance_criteria == ['AC-1']
        assert state.stories['US-002'].status == StoryStatus.unclaimed

    def test_rich_format(self, tmp_path):
        prd_data = {
            'stories': [
                {'id': 'US-001', 'title': 'Story 1'},
            ]
        }
        prd_path = tmp_path / 'prd.json'
        prd_path.write_text(json.dumps(prd_data))
        state_path = tmp_path / 'state.json'

        state = initialize_state_from_prd(prd_path, state_path)
        assert 'US-001' in state.stories

    def test_depends_on_preserved(self, tmp_path):
        prd_data = [
            {'id': 'US-001', 'title': 'Story 1'},
            {'id': 'US-002', 'title': 'Story 2', 'depends_on': ['US-001']},
        ]
        prd_path = tmp_path / 'prd.json'
        prd_path.write_text(json.dumps(prd_data))
        state_path = tmp_path / 'state.json'

        state = initialize_state_from_prd(prd_path, state_path)
        assert state.stories['US-002'].depends_on == ['US-001']


# ===========================================================================
# editing.py
# ===========================================================================


class TestEditValidation:
    def test_add_after_valid(self):
        story = _make_story()
        story.steps[0].status = StepStatus.completed
        ops = [
            AddAfterEdit(
                target_step_id='step-001',
                reason='test',
                new_steps=[NewStepSpec(type=StepType.coding, description='Extra coding')],
            )
        ]
        validate_edits(story, ops)  # should not raise

    def test_add_after_nonexistent_target(self):
        story = _make_story()
        ops = [
            AddAfterEdit(
                target_step_id='step-999',
                reason='test',
                new_steps=[NewStepSpec(type=StepType.coding, description='Extra')],
            )
        ]
        with pytest.raises(EditValidationError, match='not found'):
            validate_edits(story, ops)

    def test_add_after_final_review_rejected(self):
        story = _make_story()
        ops = [
            AddAfterEdit(
                target_step_id='step-010',
                reason='test',
                new_steps=[NewStepSpec(type=StepType.coding, description='After final')],
            )
        ]
        with pytest.raises(EditValidationError, match='cannot add steps after final_review'):
            validate_edits(story, ops)

    def test_skip_mandatory_step_rejected(self):
        story = _make_story()
        # step-006 is linting (mandatory)
        ops = [SkipEdit(target_step_id='step-006', reason='skip lint')]
        with pytest.raises(EditValidationError, match='cannot skip mandatory'):
            validate_edits(story, ops)

    def test_skip_non_mandatory_valid(self):
        story = _make_story()
        # step-009 is prune_tests (not mandatory)
        ops = [SkipEdit(target_step_id='step-009', reason='no redundant tests')]
        validate_edits(story, ops)  # should not raise

    def test_skip_non_pending_rejected(self):
        story = _make_story()
        story.steps[0].status = StepStatus.completed
        ops = [SkipEdit(target_step_id='step-001', reason='already done')]
        with pytest.raises(EditValidationError, match='can only skip pending'):
            validate_edits(story, ops)

    def test_split_valid(self):
        story = _make_story()
        ops = [
            SplitEdit(
                target_step_id='step-005',
                reason='split coding',
                replacement_steps=[
                    NewStepSpec(type=StepType.coding, description='Part 1'),
                    NewStepSpec(type=StepType.coding, description='Part 2'),
                ],
            )
        ]
        validate_edits(story, ops)

    def test_split_mandatory_rejected(self):
        story = _make_story()
        ops = [
            SplitEdit(
                target_step_id='step-006',
                reason='split linting',
                replacement_steps=[
                    NewStepSpec(type=StepType.linting, description='Part 1'),
                    NewStepSpec(type=StepType.linting, description='Part 2'),
                ],
            )
        ]
        with pytest.raises(EditValidationError, match='cannot split mandatory'):
            validate_edits(story, ops)

    def test_restart_valid(self):
        story = _make_story()
        story.steps[4].status = StepStatus.in_progress  # step-005 coding
        ops = [
            RestartEdit(
                target_step_id='step-005',
                reason='wrong approach',
                new_description='Try different approach',
            )
        ]
        validate_edits(story, ops)

    def test_restart_not_in_progress_rejected(self):
        story = _make_story()
        ops = [
            RestartEdit(
                target_step_id='step-005',
                reason='test',
                new_description='New desc',
            )
        ]
        with pytest.raises(EditValidationError, match='can only restart in_progress'):
            validate_edits(story, ops)

    def test_restart_max_exceeded(self):
        story = _make_story()
        story.steps[4].status = StepStatus.in_progress
        story.steps[4].restart_count = MAX_RESTARTS_PER_STEP
        ops = [
            RestartEdit(
                target_step_id='step-005',
                reason='test',
                new_description='New desc',
            )
        ]
        with pytest.raises(EditValidationError, match='max restarts'):
            validate_edits(story, ops)

    def test_max_steps_exceeded(self):
        story = _make_story()
        # Add 21 new steps (10 existing + 21 = 31 > 30)
        ops = [
            AddAfterEdit(
                target_step_id='step-005',
                reason='test',
                new_steps=[NewStepSpec(type=StepType.coding, description=f'Extra {i}') for i in range(21)],
            )
        ]
        with pytest.raises(EditValidationError, match='exceeding maximum'):
            validate_edits(story, ops)

    def test_reorder_valid(self):
        story = _make_story()
        pending_ids = [s.id for s in story.steps if s.status == StepStatus.pending]
        # Swap first two pending steps, keep final_review last
        new_order = [pending_ids[1], pending_ids[0], *pending_ids[2:]]
        ops = [ReorderEdit(reason='test', new_order=new_order)]
        validate_edits(story, ops)

    def test_reorder_missing_step_rejected(self):
        story = _make_story()
        ops = [ReorderEdit(reason='test', new_order=['step-001'])]
        with pytest.raises(EditValidationError, match='new_order must contain'):
            validate_edits(story, ops)

    def test_reorder_final_review_not_last_rejected(self):
        story = _make_story()
        pending_ids = [s.id for s in story.steps if s.status == StepStatus.pending]
        # Put final_review first
        fr_id = pending_ids[-1]
        reordered = [fr_id] + [pid for pid in pending_ids if pid != fr_id]
        ops = [ReorderEdit(reason='test', new_order=reordered)]
        with pytest.raises(EditValidationError, match='final_review must remain'):
            validate_edits(story, ops)

    def test_edit_description_valid(self):
        story = _make_story()
        ops = [
            EditDescriptionEdit(
                target_step_id='step-005',
                reason='clarify',
                new_description='Updated description',
            )
        ]
        validate_edits(story, ops)

    def test_edit_description_non_pending_rejected(self):
        story = _make_story()
        story.steps[4].status = StepStatus.in_progress
        ops = [
            EditDescriptionEdit(
                target_step_id='step-005',
                reason='clarify',
                new_description='Updated',
            )
        ]
        with pytest.raises(EditValidationError, match='can only edit pending'):
            validate_edits(story, ops)


class TestEditApplication:
    def test_apply_add_after(self):
        story = _make_story()
        story.steps[0].status = StepStatus.completed
        ops = [
            AddAfterEdit(
                target_step_id='step-001',
                reason='test',
                new_steps=[
                    NewStepSpec(type=StepType.coding, description='Extra coding'),
                ],
            )
        ]
        validate_edits(story, ops)
        apply_edits(story, ops)

        assert len(story.steps) == 11
        assert story.steps[1].type == StepType.coding
        assert story.steps[1].description == 'Extra coding'
        assert story.steps[1].id == 'step-011'

    def test_apply_split(self):
        story = _make_story()
        original_step_5_idx = next(i for i, s in enumerate(story.steps) if s.id == 'step-005')
        ops = [
            SplitEdit(
                target_step_id='step-005',
                reason='split',
                replacement_steps=[
                    NewStepSpec(type=StepType.coding, description='Part 1'),
                    NewStepSpec(type=StepType.coding, description='Part 2'),
                ],
            )
        ]
        validate_edits(story, ops)
        apply_edits(story, ops)

        assert len(story.steps) == 11  # 10 - 1 + 2
        assert story.steps[original_step_5_idx].description == 'Part 1'
        assert story.steps[original_step_5_idx + 1].description == 'Part 2'

    def test_apply_skip(self):
        story = _make_story()
        ops = [SkipEdit(target_step_id='step-009', reason='no redundancy')]
        validate_edits(story, ops)
        apply_edits(story, ops)

        step = story.find_step('step-009')
        assert step.status == StepStatus.skipped
        assert step.skip_reason == 'no redundancy'

    def test_apply_reorder(self):
        story = _make_story()
        pending_ids = [s.id for s in story.steps if s.status == StepStatus.pending]
        new_order = [*reversed(pending_ids[:-1]), pending_ids[-1]]
        ops = [ReorderEdit(reason='test', new_order=new_order)]
        validate_edits(story, ops)
        apply_edits(story, ops)

        current_pending = [s.id for s in story.steps if s.status == StepStatus.pending]
        assert current_pending == new_order

    def test_apply_edit_description(self):
        story = _make_story()
        ops = [
            EditDescriptionEdit(
                target_step_id='step-005',
                reason='clarify',
                new_description='Updated description',
            )
        ]
        validate_edits(story, ops)
        apply_edits(story, ops)

        step = story.find_step('step-005')
        assert step.description == 'Updated description'

    def test_apply_restart(self):
        story = _make_story()
        story.steps[4].status = StepStatus.in_progress
        story.steps[4].started_at = '2025-01-01T00:00:00Z'
        ops = [
            RestartEdit(
                target_step_id='step-005',
                reason='wrong approach',
                new_description='Try different approach',
            )
        ]
        validate_edits(story, ops)
        apply_edits(story, ops)

        step = story.find_step('step-005')
        assert step.status == StepStatus.pending
        assert step.description == 'Try different approach'
        assert step.restart_count == 1
        assert step.started_at is None


class TestEditFileParsing:
    def test_parse_edit_file(self, tmp_path):
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        edit_file = edits_dir / 'US-001.json'
        edit_file.write_text(
            json.dumps(
                [
                    {
                        'operation': 'skip',
                        'target_step_id': 'step-009',
                        'reason': 'no redundancy',
                    }
                ]
            )
        )
        ops = parse_edit_file('US-001', tmp_path)
        assert ops is not None
        assert len(ops) == 1
        assert isinstance(ops[0], SkipEdit)

    def test_parse_nonexistent_file(self, tmp_path):
        assert parse_edit_file('US-001', tmp_path) is None

    def test_remove_edit_file(self, tmp_path):
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        edit_file = edits_dir / 'US-001.json'
        edit_file.write_text('[]')
        remove_edit_file('US-001', tmp_path)
        assert not edit_file.exists()

    def test_discard_edit_file_nonexistent_noop(self, tmp_path):
        """discard_edit_file is a no-op when the file does not exist."""
        discard_edit_file('US-999', tmp_path)  # should not raise


class TestEditRoundTrip:
    """Integration tests: write edit file -> parse -> validate -> apply."""

    def test_skip_roundtrip(self, tmp_path):
        """Write a skip edit file with correct schema, parse, validate, apply."""
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        (edits_dir / 'US-001.json').write_text(
            json.dumps(
                [
                    {'operation': 'skip', 'target_step_id': 'step-009', 'reason': 'not needed'},
                ]
            )
        )

        story = _make_story()
        ops = parse_edit_file('US-001', tmp_path)
        assert ops is not None
        assert len(ops) == 1
        validate_edits(story, ops)
        apply_edits(story, ops)

        step = story.find_step('step-009')
        assert step.status == StepStatus.skipped
        assert step.skip_reason == 'not needed'
        remove_edit_file('US-001', tmp_path)
        assert not (edits_dir / 'US-001.json').exists()

    def test_multiple_skips_roundtrip(self, tmp_path):
        """Write multiple skip edits, parse, validate, apply all."""
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        (edits_dir / 'US-001.json').write_text(
            json.dumps(
                [
                    {'operation': 'skip', 'target_step_id': 'step-003', 'reason': 'skip arch'},
                    {'operation': 'skip', 'target_step_id': 'step-004', 'reason': 'skip test arch'},
                    {'operation': 'skip', 'target_step_id': 'step-005', 'reason': 'skip coding'},
                    {'operation': 'skip', 'target_step_id': 'step-009', 'reason': 'skip prune'},
                ]
            )
        )

        story = _make_story()
        ops = parse_edit_file('US-001', tmp_path)
        assert ops is not None
        assert len(ops) == 4
        validate_edits(story, ops)
        apply_edits(story, ops)

        for sid in ('step-003', 'step-004', 'step-005', 'step-009'):
            assert story.find_step(sid).status == StepStatus.skipped

    def test_wrong_field_name_step_id_fails(self, tmp_path):
        """Edit file using 'step_id' instead of 'target_step_id' must fail parsing."""
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        (edits_dir / 'US-001.json').write_text(
            json.dumps(
                [
                    {'operation': 'skip', 'step_id': 'step-009', 'reason': 'test'},
                ]
            )
        )

        with pytest.raises(ValidationError, match='target_step_id'):
            parse_edit_file('US-001', tmp_path)

    def test_add_after_roundtrip(self, tmp_path):
        """Write an add_after edit file, parse, validate, apply."""
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        (edits_dir / 'US-001.json').write_text(
            json.dumps(
                [
                    {
                        'operation': 'add_after',
                        'target_step_id': 'step-005',
                        'reason': 'need extra coding round',
                        'new_steps': [
                            {'type': 'coding', 'description': 'Extra coding pass'},
                        ],
                    }
                ]
            )
        )

        story = _make_story()
        ops = parse_edit_file('US-001', tmp_path)
        assert ops is not None
        validate_edits(story, ops)
        apply_edits(story, ops)

        assert len(story.steps) == 11
        coding_idx = next(i for i, s in enumerate(story.steps) if s.id == 'step-005')
        assert story.steps[coding_idx + 1].description == 'Extra coding pass'

    def test_discard_on_validation_failure(self, tmp_path):
        """When validation fails, discard_edit_file moves file to failed/."""
        edits_dir = tmp_path / 'workflow_edits'
        edits_dir.mkdir()
        (edits_dir / 'US-001.json').write_text(
            json.dumps(
                [
                    {'operation': 'skip', 'target_step_id': 'step-006', 'reason': 'skip mandatory'},
                ]
            )
        )

        story = _make_story()
        ops = parse_edit_file('US-001', tmp_path)
        with pytest.raises(EditValidationError):
            validate_edits(story, ops)
        discard_edit_file('US-001', tmp_path)
        assert not (edits_dir / 'US-001.json').exists()
        assert (edits_dir / 'failed' / 'US-001.json').exists()


# ===========================================================================
# scratch.py
# ===========================================================================


class TestScratch:
    def test_global_scratch_roundtrip(self, tmp_path):
        write_global_scratch('Hello world', tmp_path)
        assert read_global_scratch(tmp_path) == 'Hello world'

    def test_global_scratch_append(self, tmp_path):
        append_global_scratch('Line 1', tmp_path)
        append_global_scratch('Line 2', tmp_path)
        content = read_global_scratch(tmp_path)
        assert 'Line 1' in content
        assert 'Line 2' in content

    def test_global_scratch_empty_when_missing(self, tmp_path):
        assert read_global_scratch(tmp_path) == ''

    def test_story_scratch_roundtrip(self, tmp_path):
        write_story_scratch('US-001', 'Story context', tmp_path)
        assert read_story_scratch('US-001', tmp_path) == 'Story context'

    def test_story_scratch_append(self, tmp_path):
        append_story_scratch('US-001', 'Note 1', tmp_path)
        append_story_scratch('US-001', 'Note 2', tmp_path)
        content = read_story_scratch('US-001', tmp_path)
        assert 'Note 1' in content
        assert 'Note 2' in content

    def test_story_scratch_cleanup(self, tmp_path):
        write_story_scratch('US-001', 'data', tmp_path)
        cleanup_story_scratch('US-001', tmp_path)
        assert read_story_scratch('US-001', tmp_path) == ''

    def test_story_scratch_cleanup_nonexistent(self, tmp_path):
        cleanup_story_scratch('US-999', tmp_path)  # should not raise


# ===========================================================================
# prompts.py
# ===========================================================================


class TestPrompts:
    def test_all_step_types_have_instructions(self):
        for st in StepType:
            assert st in STEP_INSTRUCTIONS, f'Missing instructions for {st}'

    def test_compose_step_prompt_contains_story(self):
        story = _make_story()
        step = story.steps[0]
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'Test story' in prompt
        assert 'US-001' in prompt

    def test_compose_step_prompt_contains_acceptance_criteria(self):
        story = _make_story()
        step = story.steps[0]
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'AC-1' in prompt
        assert 'AC-2' in prompt

    def test_compose_step_prompt_contains_step_instructions(self):
        story = _make_story()
        step = story.steps[0]  # context_gathering
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'Context Gathering' in prompt

    def test_compose_step_prompt_includes_prior_notes(self):
        story = _make_story()
        story.steps[0].status = StepStatus.completed
        story.steps[0].notes = 'Found relevant models in profiles/models.py'
        step = story.steps[1]  # planning
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'Found relevant models' in prompt

    def test_compose_step_prompt_includes_scratch(self):
        story = _make_story()
        step = story.steps[0]
        prompt = compose_step_prompt(story, step, 'Global note', 'Story note', '')
        assert 'Global note' in prompt
        assert 'Story note' in prompt

    def test_compose_step_prompt_includes_workflow_editing_for_editable_steps(self):
        story = _make_story()
        # planning step allows editing
        step = story.steps[1]
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'workflow_edits' in prompt

    def test_compose_step_prompt_editing_section_shows_json_schema(self):
        story = _make_story()
        step = story.steps[1]  # planning — allows editing
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'target_step_id' in prompt
        assert '"operation": "skip"' in prompt
        assert '"operation": "add_after"' in prompt
        assert 'NOT `"step_id"`' in prompt

    def test_compose_step_prompt_no_editing_for_non_editable_steps(self):
        story = _make_story()
        # context_gathering does not allow editing
        step = story.steps[0]
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'workflow_edits/US-001.json' not in prompt

    def test_compose_step_prompt_lists_remaining_step_ids(self):
        story = _make_story()
        # planning (step-002) allows editing; steps 003-010 are pending after it
        step = story.steps[1]
        prompt = compose_step_prompt(story, step, '', '', '')
        assert 'Remaining Steps' in prompt
        for s in story.steps[2:]:
            assert s.id in prompt

    def test_format_remaining_steps_shows_mandatory_flag(self):
        story = _make_story()
        step = story.steps[1]  # planning
        remaining = _format_remaining_steps(story, step)
        # linting (step-006) and final_review (step-010) are mandatory
        assert 'step-006' in remaining
        assert '**(mandatory)**' in remaining
        # coding (step-005) is not mandatory
        assert 'step-005' in remaining

    def test_format_remaining_steps_excludes_skipped(self):
        story = _make_story()
        story.steps[8].status = StepStatus.skipped  # step-009 prune_tests
        step = story.steps[1]  # planning
        remaining = _format_remaining_steps(story, step)
        assert 'step-009' not in remaining


# ===========================================================================
# filelock.py
# ===========================================================================


class TestFileLock:
    def test_basic_lock_unlock(self, tmp_path):
        from multi_agent.filelock import FileLock

        lock_path = str(tmp_path / 'test.lock')
        with FileLock(lock_path):
            # Should be able to acquire and release without error
            pass

    def test_timeout_raises(self, tmp_path):
        import multiprocessing

        from multi_agent.filelock import FileLock, FileLockTimeout

        lock_path = str(tmp_path / 'test.lock')

        # Use a child process to hold the lock (fcntl locks are per-process)
        ready = multiprocessing.Event()
        release = multiprocessing.Event()

        def hold_lock():
            import fcntl

            fd = open(lock_path, 'w')
            fcntl.lockf(fd, fcntl.LOCK_EX)
            ready.set()
            release.wait(timeout=10)
            fcntl.lockf(fd, fcntl.LOCK_UN)
            fd.close()

        proc = multiprocessing.Process(target=hold_lock)
        proc.start()
        ready.wait(timeout=5)
        try:
            with pytest.raises(FileLockTimeout):
                with FileLock(lock_path, timeout=1):
                    pass
        finally:
            release.set()
            proc.join(timeout=5)


# ===========================================================================
# executor.py — _tee_stderr
# ===========================================================================


class TestTeeStderr:
    def test_basic_tee(self):
        """Lines from the pipe are written to both terminal and log file."""
        pipe = io.StringIO('line1\nline2\nline3\n')
        log_file = io.StringIO()
        terminal = io.StringIO()

        _tee_stderr(pipe, log_file, terminal)

        assert terminal.getvalue() == 'line1\nline2\nline3\n'
        assert log_file.getvalue() == 'line1\nline2\nline3\n'

    def test_empty_pipe(self):
        """An empty pipe produces no output."""
        pipe = io.StringIO('')
        log_file = io.StringIO()
        terminal = io.StringIO()

        _tee_stderr(pipe, log_file, terminal)

        assert terminal.getvalue() == ''
        assert log_file.getvalue() == ''

    def test_no_trailing_newline(self):
        """A line without a trailing newline is still captured."""
        pipe = io.StringIO('no newline')
        log_file = io.StringIO()
        terminal = io.StringIO()

        _tee_stderr(pipe, log_file, terminal)

        assert terminal.getvalue() == 'no newline'
        assert log_file.getvalue() == 'no newline'
