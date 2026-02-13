"""Tests for generate_run_dir() and _write_metadata() in the orchestrator."""

import importlib.util
import json
import re
from pathlib import Path

import pytest


# Import from the bin script using the same pattern as test_summary_log.py
_bin_path = Path(__file__).resolve().parent.parent / 'bin' / 'run_dynamic_ralph.py'
_spec = importlib.util.spec_from_file_location('run_dynamic_ralph', _bin_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

generate_run_dir = _mod.generate_run_dir
_write_metadata = _mod._write_metadata

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


class TestWriteMetadata:
    """Tests for the _write_metadata() helper."""

    def test_metadata_has_required_keys(self, tmp_path: Path) -> None:
        """metadata.json contains all expected top-level keys."""
        _write_metadata(tmp_path)
        data = json.loads((tmp_path / 'metadata.json').read_text())
        expected_keys = {
            'timestamp',
            'hostname',
            'python_version',
            'git_branch',
            'git_sha',
            'ralph_image',
            'ralph_env_vars',
        }
        assert expected_keys <= set(data.keys())

    def test_metadata_ralph_env_vars(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """RALPH_* env vars are captured in ralph_env_vars dict."""
        monkeypatch.setenv('RALPH_IMAGE', 'test-image:v1')
        monkeypatch.setenv('RALPH_SERVICE', 'myapp')
        _write_metadata(tmp_path)
        data = json.loads((tmp_path / 'metadata.json').read_text())
        assert data['ralph_image'] == 'test-image:v1'
        assert data['ralph_env_vars']['RALPH_IMAGE'] == 'test-image:v1'
        assert data['ralph_env_vars']['RALPH_SERVICE'] == 'myapp'

    def test_metadata_python_version(self, tmp_path: Path) -> None:
        """python_version matches sys.version."""
        import sys

        _write_metadata(tmp_path)
        data = json.loads((tmp_path / 'metadata.json').read_text())
        assert data['python_version'] == sys.version
