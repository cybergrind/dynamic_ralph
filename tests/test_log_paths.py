"""Tests for US-001: log/diff paths use shared_dir / 'logs' instead of Path('logs')."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from multi_agent.backend import AgentEvent, AgentResult
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


# ---------------------------------------------------------------------------
# run_dynamic_ralph.py — diff path on failure (line 382)
# ---------------------------------------------------------------------------


class TestRunDynamicRalphDiffPath:
    """Verify that the orchestrator failure diff path uses shared_dir / 'logs'."""

    @patch('bin.run_dynamic_ralph._save_diff_and_reset')
    @patch('bin.run_dynamic_ralph._print_progress')
    @patch('bin.run_dynamic_ralph.parse_edit_file', return_value=None)
    @patch('bin.run_dynamic_ralph._run_agent_docker')
    @patch('bin.run_dynamic_ralph.compose_step_prompt', return_value='prompt')
    @patch('bin.run_dynamic_ralph.read_story_scratch', return_value='')
    @patch('bin.run_dynamic_ralph.read_global_scratch', return_value='')
    @patch('bin.run_dynamic_ralph.locked_state')
    def test_diff_path_uses_shared_dir(
        self,
        mock_locked_state,
        mock_global_scratch,
        mock_story_scratch,
        mock_compose,
        mock_run_agent,
        mock_parse_edit,
        mock_print,
        mock_save_diff,
        tmp_path,
    ):
        story = _make_story()
        state = _make_state(story)
        step = story.steps[0]
        step.git_sha_at_start = 'abc123'
        shared_dir = tmp_path / 'run_ralph' / '20250101_120000'

        mock_locked_state.return_value.__enter__ = MagicMock(return_value=state)
        mock_locked_state.return_value.__exit__ = MagicMock(return_value=False)

        # Agent fails (returncode != 0)
        mock_run_agent.return_value = (
            1,  # returncode
            {'last_assistant_text': '', 'cost_usd': 0.0, 'input_tokens': 0, 'output_tokens': 0},
        )

        from bin.run_dynamic_ralph import execute_step as orchestrator_execute_step

        orchestrator_execute_step(
            story=story,
            step_id=step.id,
            agent_id=1,
            state_path=tmp_path / 'state.json',
            shared_dir=shared_dir,
            max_turns=10,
        )

        # Verify _save_diff_and_reset was called with a path under shared_dir
        mock_save_diff.assert_called_once()
        diff_path: Path = mock_save_diff.call_args[0][0]
        assert str(diff_path).startswith(str(shared_dir)), f'Expected diff_path under {shared_dir}, got {diff_path}'
        expected = shared_dir / 'logs' / 'US-001' / 'step-005.diff'
        assert diff_path == expected


# ---------------------------------------------------------------------------
# run_dynamic_ralph.py — log_path passed to _run_agent_docker (US-001)
# ---------------------------------------------------------------------------


class TestRunDynamicRalphLogPath:
    """Verify that execute_step passes the correct log_path to _run_agent_docker."""

    @patch('bin.run_dynamic_ralph._print_progress')
    @patch('bin.run_dynamic_ralph.parse_edit_file', return_value=None)
    @patch('bin.run_dynamic_ralph._run_agent_docker')
    @patch('bin.run_dynamic_ralph.compose_step_prompt', return_value='prompt')
    @patch('bin.run_dynamic_ralph.read_story_scratch', return_value='')
    @patch('bin.run_dynamic_ralph.read_global_scratch', return_value='')
    @patch('bin.run_dynamic_ralph.locked_state')
    def test_log_path_passed_to_run_agent_docker(
        self,
        mock_locked_state,
        mock_global_scratch,
        mock_story_scratch,
        mock_compose,
        mock_run_agent,
        mock_parse_edit,
        mock_print,
        tmp_path,
    ):
        story = _make_story()
        state = _make_state(story)
        shared_dir = tmp_path / 'run_ralph' / '20250101_120000'

        mock_locked_state.return_value.__enter__ = MagicMock(return_value=state)
        mock_locked_state.return_value.__exit__ = MagicMock(return_value=False)

        mock_run_agent.return_value = (
            0,
            {'last_assistant_text': '## SUMMARY\nDone.', 'cost_usd': 0.0, 'input_tokens': 0, 'output_tokens': 0},
        )

        from bin.run_dynamic_ralph import execute_step as orchestrator_execute_step

        orchestrator_execute_step(
            story=story,
            step_id='step-005',
            agent_id=1,
            state_path=tmp_path / 'state.json',
            shared_dir=shared_dir,
            max_turns=10,
        )

        # Verify _run_agent_docker was called with log_path
        mock_run_agent.assert_called_once()
        call_kwargs = mock_run_agent.call_args.kwargs
        log_path = call_kwargs.get('log_path')
        assert log_path is not None, '_run_agent_docker was not called with log_path'
        expected = shared_dir / 'logs' / 'US-001' / 'step-005.jsonl'
        assert log_path == expected


# ---------------------------------------------------------------------------
# run_dynamic_ralph.py — _run_agent_docker writes JSONL and stderr (US-001)
# ---------------------------------------------------------------------------


class TestRunAgentDockerLogging:
    """Verify that _run_agent_docker writes JSONL events and tees stderr."""

    @patch('bin.run_dynamic_ralph.get_backend')
    def test_writes_jsonl_when_log_path_provided(self, mock_get_backend, tmp_path):
        """Events with raw data are written as JSONL to log_path."""
        log_path = tmp_path / 'logs' / 'US-001' / 'step-005.jsonl'

        # Set up mock backend
        backend = MagicMock()
        mock_get_backend.return_value = backend
        backend.build_command.return_value = ['echo', 'hello']
        backend.build_docker_command.return_value = ['echo', 'hello']

        events = [
            AgentEvent(kind='assistant', text='Hello', raw={'type': 'assistant', 'message': 'Hello'}),
            AgentEvent(kind='result', text='Done', raw={'type': 'result', 'cost': 0.01}),
        ]
        backend.parse_events.return_value = iter(events)
        backend.extract_result.return_value = AgentResult(exit_code=0, final_response='Hello', cost_usd=0.01)

        # Mock Popen so no real subprocess is spawned
        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter([])
        mock_process.returncode = 0
        mock_process.wait.return_value = 0

        with patch('bin.run_dynamic_ralph.subprocess.Popen', return_value=mock_process):
            from bin.run_dynamic_ralph import _run_agent_docker

            returncode, _result_info = _run_agent_docker(
                task='test task',
                agent_id=1,
                max_turns=10,
                workspace=str(tmp_path),
                log_path=log_path,
            )

        assert returncode == 0
        assert log_path.exists(), f'Expected JSONL log at {log_path}'
        lines = log_path.read_text().strip().splitlines()
        assert len(lines) == 2
        # Each line should be valid JSON matching the event raw data
        assert json.loads(lines[0]) == {'type': 'assistant', 'message': 'Hello'}
        assert json.loads(lines[1]) == {'type': 'result', 'cost': 0.01}

    @patch('bin.run_dynamic_ralph.get_backend')
    def test_writes_stderr_log_when_log_path_provided(self, mock_get_backend, tmp_path):
        """Stderr output is captured to a .stderr.log file."""
        log_path = tmp_path / 'logs' / 'US-001' / 'step-005.jsonl'
        stderr_log_path = log_path.with_suffix('.stderr.log')

        backend = MagicMock()
        mock_get_backend.return_value = backend
        backend.build_command.return_value = ['echo', 'hello']
        backend.build_docker_command.return_value = ['echo', 'hello']
        backend.parse_events.return_value = iter([])
        backend.extract_result.return_value = AgentResult(exit_code=0, final_response='')

        # Simulate stderr lines (like npm notices)
        stderr_lines = [
            'npm notice New major version of npm available! 10.8.2 -> 11.9.0\n',
            'npm notice Run `npm install -g npm@11.9.0` to update!\n',
        ]

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.stderr = iter(stderr_lines)
        mock_process.returncode = 0
        mock_process.wait.return_value = 0

        with patch('bin.run_dynamic_ralph.subprocess.Popen', return_value=mock_process):
            from bin.run_dynamic_ralph import _run_agent_docker

            _run_agent_docker(
                task='test task',
                agent_id=1,
                max_turns=10,
                workspace=str(tmp_path),
                log_path=log_path,
            )

        assert stderr_log_path.exists(), f'Expected stderr log at {stderr_log_path}'
        content = stderr_log_path.read_text()
        assert 'npm notice New major version' in content
        assert 'npm install -g npm@11.9.0' in content

    @patch('bin.run_dynamic_ralph.get_backend')
    def test_no_log_files_when_log_path_is_none(self, mock_get_backend, tmp_path):
        """When log_path is None, no log files are created (backward-compat)."""
        backend = MagicMock()
        mock_get_backend.return_value = backend
        backend.build_command.return_value = ['echo', 'hello']
        backend.build_docker_command.return_value = ['echo', 'hello']
        backend.parse_events.return_value = iter([])
        backend.extract_result.return_value = AgentResult(exit_code=0, final_response='')

        mock_process = MagicMock()
        mock_process.stdout = iter([])
        mock_process.returncode = 0
        mock_process.wait.return_value = 0

        with patch('bin.run_dynamic_ralph.subprocess.Popen', return_value=mock_process):
            from bin.run_dynamic_ralph import _run_agent_docker

            _run_agent_docker(
                task='test task',
                agent_id=1,
                max_turns=10,
                workspace=str(tmp_path),
                log_path=None,
            )

        # No log files should have been created in tmp_path
        log_files = list(tmp_path.rglob('*.jsonl')) + list(tmp_path.rglob('*.stderr.log'))
        assert len(log_files) == 0, f'Unexpected log files created: {log_files}'
