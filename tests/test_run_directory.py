"""Tests for generate_run_dir() in the orchestrator."""

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
