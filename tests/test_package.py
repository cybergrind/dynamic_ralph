"""Verify wheel and sdist contain the right files and exclude unwanted ones."""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import ClassVar

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _wheel_path() -> Path:
    """Build and return path to the wheel."""
    result = subprocess.run(
        ['uv', 'build', '--wheel', '--out-dir', '/tmp/ralph_test_pkg'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.strip().endswith('.whl'):
            return Path(line.strip())
    # fallback: find the file
    out = Path('/tmp/ralph_test_pkg')
    wheels = list(out.glob('*.whl'))
    assert wheels, f'No wheel found in {out}'
    return wheels[0]


@lru_cache(maxsize=1)
def _sdist_path() -> Path:
    """Build and return path to the sdist."""
    subprocess.run(
        ['uv', 'build', '--sdist', '--out-dir', '/tmp/ralph_test_pkg'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    out = Path('/tmp/ralph_test_pkg')
    sdists = list(out.glob('*.tar.gz'))
    assert sdists, f'No sdist found in {out}'
    return sdists[0]


@pytest.fixture(scope='module')
def wheel_files() -> set[str]:
    whl = _wheel_path()
    with zipfile.ZipFile(whl) as zf:
        return set(zf.namelist())


@pytest.fixture(scope='module')
def sdist_files() -> set[str]:
    sdist = _sdist_path()
    with tarfile.open(sdist) as tf:
        return {m.name for m in tf.getmembers()}


# ---------------------------------------------------------------------------
# Wheel tests
# ---------------------------------------------------------------------------


class TestWheelContents:
    """Verify the wheel includes required files and excludes unwanted ones."""

    REQUIRED_IN_WHEEL: ClassVar[list[str]] = [
        # Python packages
        'multi_agent/__init__.py',
        'multi_agent/docker.py',
        'multi_agent/constants.py',
        'multi_agent/prompts.py',
        'multi_agent/backends/claude_code.py',
        'multi_agent/workflow/executor.py',
        'bin/__init__.py',
        'bin/run_dynamic_ralph.py',
        'bin/run_agent.py',
        # Package data — Docker build files
        'multi_agent/_data/docker/Dockerfile',
        'multi_agent/_data/pyproject.toml',
        'multi_agent/_data/uv.lock',
        'multi_agent/_data/.pre-commit-config.yaml',
        # Package data — docs and CLAUDE.md
        'multi_agent/_data/CLAUDE.md',
        'multi_agent/_data/docs/dynamic_ralph.md',
        'multi_agent/_data/docs/multi_agent_codex.md',
        'multi_agent/_data/docs/identities/i_consul.md',
        # Package data — skills
        'multi_agent/_data/skills/multi-agent/SKILL.md',
        'multi_agent/_data/skills/multi-agent/orchestrate.py',
    ]

    FORBIDDEN_PATTERNS_IN_WHEEL: ClassVar[list[str]] = [
        '.claude/',
        'tests/',
        '.git/',
        '.env',
        'run_ralph/',
        '__pycache__/',
    ]

    def test_required_files_present(self, wheel_files):
        missing = [f for f in self.REQUIRED_IN_WHEEL if f not in wheel_files]
        assert not missing, 'Missing from wheel:\n' + '\n'.join(missing)

    def test_forbidden_patterns_absent(self, wheel_files):
        violations = []
        for f in wheel_files:
            for pat in self.FORBIDDEN_PATTERNS_IN_WHEEL:
                if pat in f:
                    violations.append(f)
                    break
        assert not violations, 'Unwanted files in wheel:\n' + '\n'.join(violations)

    def test_identities_complete(self, wheel_files):
        """All identity files from docs/identities/ must be in the wheel."""
        source_identities = {p.name for p in (PROJECT_ROOT / 'docs' / 'identities').glob('*.md')}
        wheel_identities = {f.split('/')[-1] for f in wheel_files if f.startswith('multi_agent/_data/docs/identities/')}
        missing = source_identities - wheel_identities
        assert not missing, f'Identity files missing from wheel: {missing}'


# ---------------------------------------------------------------------------
# Sdist tests
# ---------------------------------------------------------------------------


class TestSdistContents:
    """Verify the sdist includes source and excludes unwanted files."""

    FORBIDDEN_PATTERNS_IN_SDIST: ClassVar[list[str]] = [
        '.claude/',
        'run_ralph/',
        '__pycache__/',
    ]

    def test_forbidden_patterns_absent(self, sdist_files):
        violations = []
        for f in sdist_files:
            for pat in self.FORBIDDEN_PATTERNS_IN_SDIST:
                if pat in f:
                    violations.append(f)
                    break
        assert not violations, 'Unwanted files in sdist:\n' + '\n'.join(violations)

    def test_source_files_present(self, sdist_files):
        """Sdist should include Python source and key project files."""
        files = sdist_files
        # Check for a few key files (paths are prefixed with package-version/)
        has_init = any('multi_agent/__init__.py' in f for f in files)
        has_pyproject = any('pyproject.toml' in f for f in files)
        has_dockerfile = any('docker/Dockerfile' in f for f in files)
        assert has_init, 'multi_agent/__init__.py not in sdist'
        assert has_pyproject, 'pyproject.toml not in sdist'
        assert has_dockerfile, 'docker/Dockerfile not in sdist'
