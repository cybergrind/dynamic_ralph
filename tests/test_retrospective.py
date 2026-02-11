"""Tests for bin/run_retrospective.py."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from multi_agent.backend import AgentResult
from multi_agent.workflow.models import (
    Step,
    StepStatus,
    StepType,
    StoryStatus,
    StoryWorkflow,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Import the bin script via importlib (no __init__.py in bin/)
# ---------------------------------------------------------------------------

_bin_path = Path(__file__).resolve().parent.parent / 'bin' / 'run_retrospective.py'
_spec = importlib.util.spec_from_file_location('run_retrospective', _bin_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

validate_run_dir = _mod.validate_run_dir
build_state_digest = _mod.build_state_digest
collect_log_files = _mod.collect_log_files
build_retrospective_prompt = _mod.build_retrospective_prompt
launch_agent = _mod.launch_agent
main = _mod.main


# ---------------------------------------------------------------------------
# Shared fixture: realistic run directory
# ---------------------------------------------------------------------------


def _sample_state() -> WorkflowState:
    """Create a sample WorkflowState for testing."""
    return WorkflowState(
        version=1,
        created_at='2025-01-01T00:00:00',
        prd_file='prd.json',
        stories={
            'US-001': StoryWorkflow(
                story_id='US-001',
                title='First story',
                status=StoryStatus.completed,
                steps=[
                    Step(
                        id='step-001',
                        type=StepType.coding,
                        status=StepStatus.completed,
                        started_at='2025-01-01T00:01:00',
                        completed_at='2025-01-01T00:05:00',
                        cost_usd=0.05,
                        input_tokens=1000,
                        output_tokens=500,
                    ),
                ],
            ),
            'US-002': StoryWorkflow(
                story_id='US-002',
                title='Failed story',
                status=StoryStatus.failed,
                steps=[
                    Step(
                        id='step-001',
                        type=StepType.coding,
                        status=StepStatus.failed,
                        started_at='2025-01-01T00:10:00',
                        completed_at='2025-01-01T00:15:00',
                        error='Agent exited with code 1',
                        notes='Something went wrong',
                    ),
                ],
            ),
        },
    )


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """Create a realistic run directory with summary.log, workflow_state.json, and logs."""
    rd = tmp_path / 'run_ralph' / '20250101T000000_abcd1234'
    rd.mkdir(parents=True)

    # summary.log
    (rd / 'summary.log').write_text(
        '[2025-01-01 00:00:00 UTC] Run started\n'
        '[2025-01-01 00:05:00 UTC] US-001 completed\n'
        '[2025-01-01 00:15:00 UTC] US-002 FAILED\n'
    )

    # workflow_state.json
    state = _sample_state()
    (rd / 'workflow_state.json').write_text(json.dumps(state.model_dump(), indent=2))

    # logs
    log_dir = rd / 'logs' / 'US-002'
    log_dir.mkdir(parents=True)
    (log_dir / 'step-001.jsonl').write_text('{"type":"assistant","text":"hello"}\n')
    (log_dir / 'step-001.stderr.log').write_text('warning: something\n')
    (log_dir / 'step-001.diff').write_text('diff --git a/foo b/foo\n')

    return rd


# ---------------------------------------------------------------------------
# TestValidateRunDir
# ---------------------------------------------------------------------------


class TestValidateRunDir:
    def test_valid_dir(self, run_dir: Path) -> None:
        """No exception when run directory contains required files."""
        validate_run_dir(run_dir)  # should not raise

    def test_missing_dir(self, tmp_path: Path) -> None:
        """SystemExit when run directory does not exist."""
        with pytest.raises(SystemExit):
            validate_run_dir(tmp_path / 'nonexistent')

    def test_missing_summary_log(self, run_dir: Path) -> None:
        """SystemExit when summary.log is missing."""
        (run_dir / 'summary.log').unlink()
        with pytest.raises(SystemExit):
            validate_run_dir(run_dir)

    def test_missing_workflow_state(self, run_dir: Path) -> None:
        """SystemExit when workflow_state.json is missing."""
        (run_dir / 'workflow_state.json').unlink()
        with pytest.raises(SystemExit):
            validate_run_dir(run_dir)


# ---------------------------------------------------------------------------
# TestBuildStateDigest
# ---------------------------------------------------------------------------


class TestBuildStateDigest:
    def test_contains_story_ids(self) -> None:
        state = _sample_state()
        digest = build_state_digest(state)
        assert 'US-001' in digest
        assert 'US-002' in digest

    def test_contains_step_statuses(self) -> None:
        state = _sample_state()
        digest = build_state_digest(state)
        assert 'completed' in digest
        assert 'failed' in digest

    def test_contains_error_messages(self) -> None:
        state = _sample_state()
        digest = build_state_digest(state)
        assert 'Agent exited with code 1' in digest

    def test_contains_failed_stories_section(self) -> None:
        state = _sample_state()
        digest = build_state_digest(state)
        assert 'Failed stories' in digest

    def test_contains_cost_info(self) -> None:
        state = _sample_state()
        digest = build_state_digest(state)
        assert '$0.0500' in digest

    def test_empty_state(self) -> None:
        state = WorkflowState(version=1, created_at='now', prd_file='', stories={})
        digest = build_state_digest(state)
        assert '0 stories' in digest

    def test_truncates_long_notes(self) -> None:
        state = WorkflowState(
            version=1,
            created_at='now',
            prd_file='',
            stories={
                'S1': StoryWorkflow(
                    story_id='S1',
                    title='Test',
                    status=StoryStatus.completed,
                    steps=[
                        Step(
                            id='step-001',
                            type=StepType.coding,
                            status=StepStatus.completed,
                            notes='x' * 300,
                        ),
                    ],
                ),
            },
        )
        digest = build_state_digest(state)
        assert '...' in digest


# ---------------------------------------------------------------------------
# TestCollectLogFiles
# ---------------------------------------------------------------------------


class TestCollectLogFiles:
    def test_finds_all_log_types(self, run_dir: Path) -> None:
        files = collect_log_files(run_dir)
        suffixes = {f.suffix for f in files}
        assert '.jsonl' in suffixes
        assert '.log' in suffixes
        assert '.diff' in suffixes

    def test_returns_sorted(self, run_dir: Path) -> None:
        files = collect_log_files(run_dir)
        assert files == sorted(files)

    def test_empty_when_no_logs_dir(self, tmp_path: Path) -> None:
        files = collect_log_files(tmp_path)
        assert files == []

    def test_ignores_non_log_files(self, run_dir: Path) -> None:
        (run_dir / 'logs' / 'US-002' / 'notes.txt').write_text('ignore me')
        files = collect_log_files(run_dir)
        names = [f.name for f in files]
        assert 'notes.txt' not in names


# ---------------------------------------------------------------------------
# TestBuildRetrospectivePrompt
# ---------------------------------------------------------------------------


class TestBuildRetrospectivePrompt:
    def test_contains_run_dir(self, run_dir: Path) -> None:
        prompt = build_retrospective_prompt(run_dir, 'summary', 'digest', [])
        assert str(run_dir.resolve()) in prompt

    def test_contains_summary_log(self) -> None:
        prompt = build_retrospective_prompt(Path('/tmp/test'), 'MY_SUMMARY_CONTENT', 'digest', [])
        assert 'MY_SUMMARY_CONTENT' in prompt

    def test_contains_state_digest(self) -> None:
        prompt = build_retrospective_prompt(Path('/tmp/test'), 'summary', 'MY_DIGEST_CONTENT', [])
        assert 'MY_DIGEST_CONTENT' in prompt

    def test_contains_log_file_paths(self, run_dir: Path) -> None:
        log_files = [run_dir / 'logs' / 'step-001.jsonl']
        prompt = build_retrospective_prompt(run_dir, 'summary', 'digest', log_files)
        assert 'step-001.jsonl' in prompt

    def test_contains_three_phases(self) -> None:
        prompt = build_retrospective_prompt(Path('/tmp/test'), 'summary', 'digest', [])
        assert 'Phase 1: Diagnose' in prompt
        assert 'Phase 2: Fix' in prompt
        assert 'Phase 3: Verify' in prompt

    def test_contains_retrospective_md_path(self) -> None:
        rd = Path('/tmp/test')
        prompt = build_retrospective_prompt(rd, 'summary', 'digest', [])
        assert 'retrospective.md' in prompt

    def test_contains_protected_files_warning(self) -> None:
        prompt = build_retrospective_prompt(Path('/tmp/test'), 'summary', 'digest', [])
        assert 'DO NOT delete' in prompt
        assert 'workflow_state.json' in prompt

    def test_no_log_files_message(self) -> None:
        prompt = build_retrospective_prompt(Path('/tmp/test'), 'summary', 'digest', [])
        assert 'no log files found' in prompt


# ---------------------------------------------------------------------------
# TestLaunchAgent
# ---------------------------------------------------------------------------


class TestLaunchAgent:
    @patch.object(_mod, 'get_backend')
    @patch.object(_mod, 'display_agent_event')
    @patch('subprocess.Popen')
    def test_launch_returns_agent_result(self, mock_popen, mock_display, mock_get_backend, tmp_path: Path) -> None:
        """launch_agent returns an AgentResult from the backend."""
        # Set up mocks
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend
        mock_backend.build_command.return_value = ['claude', '--prompt', 'test']
        mock_backend.build_docker_command.return_value = ['docker', 'run', 'claude']

        # Mock process
        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter([])
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        # Mock backend parse/extract
        mock_backend.parse_events.return_value = iter([])
        expected_result = AgentResult(exit_code=0, cost_usd=0.1)
        mock_backend.extract_result.return_value = expected_result

        log_path = tmp_path / 'logs' / 'retro.jsonl'
        result = launch_agent('test prompt', log_path)

        assert result.exit_code == 0
        assert result.cost_usd == 0.1
        mock_backend.build_command.assert_called_once()

    @patch.object(_mod, 'get_backend')
    @patch.object(_mod, 'display_agent_event')
    @patch('subprocess.Popen')
    def test_launch_creates_log_files(self, mock_popen, mock_display, mock_get_backend, tmp_path: Path) -> None:
        """launch_agent creates .jsonl and .stderr.log files."""
        mock_backend = MagicMock()
        mock_get_backend.return_value = mock_backend
        mock_backend.build_command.return_value = ['test']
        mock_backend.build_docker_command.return_value = ['test']

        mock_proc = MagicMock()
        mock_proc.stdout = iter([])
        mock_proc.stderr = iter([])
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        mock_backend.parse_events.return_value = iter([])
        mock_backend.extract_result.return_value = AgentResult(exit_code=0)

        log_path = tmp_path / 'logs' / 'retro.jsonl'
        launch_agent('test prompt', log_path)

        assert log_path.exists()
        assert log_path.with_suffix('.stderr.log').exists()


# ---------------------------------------------------------------------------
# TestMain (CLI integration)
# ---------------------------------------------------------------------------


class TestMain:
    @patch.object(_mod, 'launch_agent')
    @patch.object(_mod, 'image_exists', return_value=True)
    def test_main_success(self, mock_img, mock_launch, run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() exits cleanly on successful agent run."""
        mock_launch.return_value = AgentResult(exit_code=0, cost_usd=0.05)
        monkeypatch.setattr('sys.argv', ['run_retrospective.py', str(run_dir)])

        main()  # should not raise

        mock_launch.assert_called_once()
        # Verify prompt was constructed (first positional arg to launch_agent)
        call_args = mock_launch.call_args
        prompt = call_args[0][0] if call_args[0] else call_args[1].get('prompt', '')
        assert 'Retrospective Analysis' in prompt

    @patch.object(_mod, 'launch_agent')
    @patch.object(_mod, 'image_exists', return_value=True)
    def test_main_failure_exits_nonzero(
        self, mock_img, mock_launch, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() exits with code 1 when agent fails."""
        mock_launch.return_value = AgentResult(exit_code=1)
        monkeypatch.setattr('sys.argv', ['run_retrospective.py', str(run_dir)])

        with pytest.raises(SystemExit, match='1'):
            main()

    @patch.object(_mod, 'image_exists', return_value=True)
    def test_main_invalid_dir(self, mock_img, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() exits with error for nonexistent directory."""
        monkeypatch.setattr('sys.argv', ['run_retrospective.py', str(tmp_path / 'no_such_dir')])

        with pytest.raises(SystemExit):
            main()

    @patch.object(_mod, 'launch_agent')
    @patch.object(_mod, 'build_image')
    @patch.object(_mod, 'image_exists', return_value=False)
    def test_main_builds_image_when_missing(
        self, mock_exists, mock_build, mock_launch, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() calls build_image() when image doesn't exist."""
        mock_launch.return_value = AgentResult(exit_code=0, cost_usd=0.0)
        monkeypatch.setattr('sys.argv', ['run_retrospective.py', str(run_dir)])

        main()

        mock_build.assert_called_once()
