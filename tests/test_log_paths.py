"""Tests for US-001: log/diff paths use shared_dir / 'logs' instead of Path('logs')."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from multi_agent.backend import AgentResult
from multi_agent.workflow.models import (
    Step,
    StepType,
    StoryWorkflow,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_story(story_id: str = 'US-001') -> StoryWorkflow:
    step = Step(id='step-005', type=StepType.coding, description='Implement')
    return StoryWorkflow(
        story_id=story_id,
        title='Test story',
        description='desc',
        steps=[step],
    )


def _make_state(story: StoryWorkflow) -> WorkflowState:
    return WorkflowState(stories={story.story_id: story})


# ---------------------------------------------------------------------------
# executor.py — JSONL log path (line 294)
# ---------------------------------------------------------------------------


class TestExecutorLogPath:
    """Verify that the JSONL log path is under shared_dir / 'logs'."""

    @patch('multi_agent.workflow.executor._launch_agent')
    @patch('multi_agent.workflow.executor._git_current_sha', return_value='abc123')
    @patch('multi_agent.workflow.executor.locked_state')
    @patch('multi_agent.workflow.executor.read_global_scratch', return_value='')
    @patch('multi_agent.workflow.executor.read_story_scratch', return_value='')
    @patch('multi_agent.workflow.executor.compose_step_prompt', return_value='prompt')
    @patch('multi_agent.workflow.executor.append_story_scratch')
    def test_jsonl_log_uses_shared_dir(
        self,
        mock_append_scratch,
        mock_prompt,
        mock_story_scratch,
        mock_global_scratch,
        mock_locked_state,
        mock_sha,
        mock_launch,
        tmp_path,
    ):
        story = _make_story()
        state = _make_state(story)
        step = story.steps[0]
        shared_dir = tmp_path / 'run_ralph' / '20250101_120000'

        # locked_state is used as a context manager
        mock_locked_state.return_value.__enter__ = MagicMock(return_value=state)
        mock_locked_state.return_value.__exit__ = MagicMock(return_value=False)

        # Agent succeeds
        mock_launch.return_value = AgentResult(
            exit_code=0,
            final_response='## SUMMARY\nDone.',
        )

        from multi_agent.workflow.executor import execute_step

        execute_step(
            story=story,
            step=step,
            agent_id=1,
            state_path=tmp_path / 'state.json',
            shared_dir=shared_dir,
        )

        # Verify the log_path passed to _launch_agent is under shared_dir
        call_kwargs = mock_launch.call_args
        log_path: Path = call_kwargs.kwargs.get('log_path') or call_kwargs[1].get('log_path')
        if log_path is None:
            # positional arg: _launch_agent(prompt, agent_id, max_turns, log_path, timeout)
            log_path = call_kwargs[0][3]

        assert str(log_path).startswith(str(shared_dir)), f'Expected log_path under {shared_dir}, got {log_path}'
        expected = shared_dir / 'logs' / 'US-001' / 'step-005.jsonl'
        assert log_path == expected


# ---------------------------------------------------------------------------
# executor.py — diff path on failure (line 361)
# ---------------------------------------------------------------------------


class TestExecutorDiffPath:
    """Verify that the failure diff path is under shared_dir / 'logs'."""

    @patch('multi_agent.workflow.executor._git_reset_hard')
    @patch('multi_agent.workflow.executor._git_save_diff')
    @patch('multi_agent.workflow.executor.discard_edit_file')
    @patch('multi_agent.workflow.executor._launch_agent')
    @patch('multi_agent.workflow.executor._git_current_sha', return_value='abc123')
    @patch('multi_agent.workflow.executor.locked_state')
    @patch('multi_agent.workflow.executor.read_global_scratch', return_value='')
    @patch('multi_agent.workflow.executor.read_story_scratch', return_value='')
    @patch('multi_agent.workflow.executor.compose_step_prompt', return_value='prompt')
    def test_diff_path_uses_shared_dir(
        self,
        mock_prompt,
        mock_story_scratch,
        mock_global_scratch,
        mock_locked_state,
        mock_sha,
        mock_launch,
        mock_discard,
        mock_save_diff,
        mock_reset,
        tmp_path,
    ):
        story = _make_story()
        state = _make_state(story)
        step = story.steps[0]
        shared_dir = tmp_path / 'run_ralph' / '20250101_120000'

        mock_locked_state.return_value.__enter__ = MagicMock(return_value=state)
        mock_locked_state.return_value.__exit__ = MagicMock(return_value=False)

        # Agent fails
        mock_launch.return_value = AgentResult(
            exit_code=1,
            completion_status='failed',
            final_response='',
        )

        from multi_agent.workflow.executor import execute_step

        execute_step(
            story=story,
            step=step,
            agent_id=1,
            state_path=tmp_path / 'state.json',
            shared_dir=shared_dir,
        )

        # Verify _git_save_diff was called with a path under shared_dir
        mock_save_diff.assert_called_once()
        diff_path: Path = mock_save_diff.call_args[0][0]
        assert str(diff_path).startswith(str(shared_dir)), f'Expected diff_path under {shared_dir}, got {diff_path}'
        expected = shared_dir / 'logs' / 'US-001' / 'step-005.diff'
        assert diff_path == expected
