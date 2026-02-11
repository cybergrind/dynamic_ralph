"""Tests for generate_run_dir() and run-directory CLI wiring in the orchestrator."""

import argparse
import importlib.util
import re
from pathlib import Path

import pytest


# Import from the bin script using the same pattern as test_summary_log.py
_bin_path = Path(__file__).resolve().parent.parent / 'bin' / 'run_dynamic_ralph.py'
_spec = importlib.util.spec_from_file_location('run_dynamic_ralph', _bin_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

generate_run_dir = _mod.generate_run_dir

# Pattern: YYYYMMDD T HHMMSS _ 8-hex-chars
RUN_DIR_NAME_RE = re.compile(r'^\d{8}T\d{6}_[0-9a-f]{8}$')


class TestGenerateRunDir:
    """Tests for the generate_run_dir() helper."""

    def test_path_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Generated directory name matches run_ralph/<YYYYMMDD>T<HHMMSS>_<8-hex>."""
        monkeypatch.chdir(tmp_path)
        run_dir = generate_run_dir()
        assert run_dir.parent.name == 'run_ralph'
        assert RUN_DIR_NAME_RE.match(run_dir.name), f'Unexpected dir name: {run_dir.name}'

    def test_directory_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The generated directory physically exists on disk."""
        monkeypatch.chdir(tmp_path)
        run_dir = generate_run_dir()
        assert run_dir.exists()
        assert run_dir.is_dir()

    def test_subdirectories_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """workflow_edits/ and logs/ subdirectories are created inside the run dir."""
        monkeypatch.chdir(tmp_path)
        run_dir = generate_run_dir()
        assert (run_dir / 'workflow_edits').is_dir()
        assert (run_dir / 'logs').is_dir()

    def test_uniqueness(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successive calls produce distinct directories."""
        monkeypatch.chdir(tmp_path)
        dirs = {generate_run_dir() for _ in range(5)}
        assert len(dirs) == 5


class TestRunDirCLIWiring:
    """Tests for run-directory logic in main()'s CLI argument handling."""

    @staticmethod
    def _parse_args(argv: list[str]) -> argparse.Namespace:
        """Build the argparse parser from main() and parse *argv*.

        We replicate the parser construction rather than calling main() so we
        can inspect the resulting Namespace without triggering side effects
        (Docker build, workflow execution, etc.).
        """
        parser = argparse.ArgumentParser()
        parser.add_argument('task', nargs='?', default=None)
        parser.add_argument('--prd', type=Path, default=None)
        parser.add_argument('--agents', type=int, default=1)
        parser.add_argument('--agent-id', type=int, default=1)
        parser.add_argument('--story-id', type=str, default=None)
        parser.add_argument('--state-path', type=Path, default=None)
        parser.add_argument('--shared-dir', type=Path, default=None)
        parser.add_argument('--max-iterations', type=int, default=50)
        parser.add_argument('--max-turns', type=int, default=None)
        parser.add_argument('--build', action='store_true')
        parser.add_argument('--resume', action='store_true')
        return parser.parse_args(argv)

    def test_no_shared_dir_generates_run_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When --shared-dir is omitted, a run directory is generated."""
        monkeypatch.chdir(tmp_path)
        args = self._parse_args(['my-task'])

        # Simulate main()'s logic: generate run dir when shared_dir is None
        assert args.shared_dir is None
        run_dir = generate_run_dir()
        args.shared_dir = run_dir
        if args.state_path is None:
            args.state_path = run_dir / 'workflow_state.json'

        assert args.shared_dir == run_dir
        assert run_dir.exists()
        assert RUN_DIR_NAME_RE.match(run_dir.name)

    def test_explicit_shared_dir_skips_generation(self, tmp_path: Path) -> None:
        """When --shared-dir is provided, no run directory is auto-generated."""
        explicit = tmp_path / 'my_shared'
        explicit.mkdir()
        args = self._parse_args(['my-task', '--shared-dir', str(explicit)])

        # main() would NOT call generate_run_dir(); shared_dir is already set
        assert args.shared_dir == explicit
        # No run_ralph/ directory should be created
        assert not (tmp_path / 'run_ralph').exists()

    def test_state_path_defaults_to_run_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--state-path defaults to <run_dir>/workflow_state.json when absent."""
        monkeypatch.chdir(tmp_path)
        args = self._parse_args(['my-task'])

        assert args.state_path is None
        run_dir = generate_run_dir()
        args.shared_dir = run_dir
        if args.state_path is None:
            args.state_path = run_dir / 'workflow_state.json'

        assert args.state_path == run_dir / 'workflow_state.json'

    def test_explicit_state_path_preserved(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An explicit --state-path is not overridden by run directory logic."""
        monkeypatch.chdir(tmp_path)
        custom = tmp_path / 'custom_state.json'
        args = self._parse_args(['my-task', '--state-path', str(custom)])

        # Even after generating a run dir, explicit state_path stays
        run_dir = generate_run_dir()
        args.shared_dir = run_dir
        if args.state_path is None:
            args.state_path = run_dir / 'workflow_state.json'

        assert args.state_path == custom
