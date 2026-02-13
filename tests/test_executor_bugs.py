"""Tests for executor bug fixes (git helpers + timeout rollback)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


# ===========================================================================
# Bug 2: _git_current_sha raises on failure
# ===========================================================================


class TestGitCurrentSha:
    @patch('multi_agent.workflow.executor.subprocess.run')
    def test_raises_on_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=128,
            stdout='',
            stderr='fatal: not a git repository',
        )
        from multi_agent.workflow.executor import _git_current_sha

        with pytest.raises(RuntimeError, match='git rev-parse HEAD failed'):
            _git_current_sha()

    @patch('multi_agent.workflow.executor.subprocess.run')
    def test_raises_on_empty_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='   \n',
            stderr='',
        )
        from multi_agent.workflow.executor import _git_current_sha

        with pytest.raises(RuntimeError, match='returned empty output'):
            _git_current_sha()

    @patch('multi_agent.workflow.executor.subprocess.run')
    def test_returns_sha_on_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='abc123def456\n',
            stderr='',
        )
        from multi_agent.workflow.executor import _git_current_sha

        assert _git_current_sha() == 'abc123def456'


# ===========================================================================
# Bug 3: _git_reset_hard hardened
# ===========================================================================


class TestGitResetHard:
    @patch('multi_agent.workflow.executor.subprocess.run')
    def test_skips_on_empty_sha(self, mock_run):
        from multi_agent.workflow.executor import _git_reset_hard

        _git_reset_hard('')
        mock_run.assert_not_called()

    @patch('multi_agent.workflow.executor.subprocess.run')
    def test_calls_reset_and_clean_on_valid_sha(self, mock_run):
        from multi_agent.workflow.executor import _git_reset_hard

        _git_reset_hard('abc123')
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0][0][0] == ['git', 'reset', '--hard', 'abc123']
        assert mock_run.call_args_list[1][0][0] == ['git', 'clean', '-fd']

    @patch('multi_agent.workflow.executor.subprocess.run')
    def test_does_not_raise_on_subprocess_failure(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, 'git')
        from multi_agent.workflow.executor import _git_reset_hard

        # Should not raise — error is caught and logged
        _git_reset_hard('abc123')


# ===========================================================================
# Bug 1: Timeout branch calls rollback
# ===========================================================================


class TestTimeoutRollback:
    """Verify that the timeout branch in execute_step rolls back git."""

    @patch('multi_agent.workflow.executor._git_reset_hard')
    @patch('multi_agent.workflow.executor._git_save_diff')
    @patch('multi_agent.workflow.executor.discard_edit_file')
    @patch('multi_agent.workflow.executor._launch_agent')
    @patch('multi_agent.workflow.executor._git_current_sha', return_value='abc123')
    @patch('multi_agent.workflow.executor.locked_state')
    @patch('multi_agent.workflow.executor.read_global_scratch', return_value='')
    @patch('multi_agent.workflow.executor.read_story_scratch', return_value='')
    @patch('multi_agent.workflow.executor.compose_step_prompt', return_value='prompt')
    def test_timeout_calls_rollback(
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

        # Agent timed out
        mock_launch.return_value = AgentResult(
            exit_code=0,
            final_response='',
            timed_out=True,
        )

        from multi_agent.workflow.executor import execute_step

        result = execute_step(
            story=story,
            step=step,
            agent_id=1,
            state_path=tmp_path / 'state.json',
            shared_dir=shared_dir,
        )

        # Verify rollback was performed
        mock_discard.assert_called_once_with('US-001', shared_dir)
        mock_save_diff.assert_called_once()
        diff_path: Path = mock_save_diff.call_args[0][0]
        assert diff_path == shared_dir / 'logs' / 'US-001' / 'step-005.timeout.diff'
        mock_reset.assert_called_once_with('abc123')

        # Step should be cancelled
        assert result.status.value == 'cancelled'
