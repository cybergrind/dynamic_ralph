"""Tests for third-party mode support.

Covers RALPH_MODE auto-detection, validation, system prompt building,
Docker command RALPH_MODE propagation, CLI --dev flag, and Docker build
context resolution.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# TestRalphModeAutoDetect
# ---------------------------------------------------------------------------


class TestRalphModeAutoDetect:
    """Test _detect_ralph_mode() auto-detection logic."""

    def _detect(self, **env_overrides):
        """Import and call _detect_ralph_mode with a clean module."""
        import importlib

        import multi_agent.constants as mod

        with patch.dict('os.environ', env_overrides, clear=False):
            importlib.reload(mod)
            return mod._detect_ralph_mode()

    def test_auto_detects_self_from_ralph_repo(self):
        """Git remote containing 'dynamic_ralph' triggers self mode."""
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='git@github.com:user/dynamic_ralph.git\n'
        )
        with (
            patch.dict('os.environ', {}, clear=False),
            patch('subprocess.run', return_value=fake_result),
        ):
            from multi_agent.constants import _detect_ralph_mode

            # Remove explicit RALPH_MODE so auto-detect runs
            with patch.dict('os.environ', {'RALPH_MODE': ''}, clear=False):
                # Empty string is falsy, so auto-detect triggers
                pass
            # Call with no RALPH_MODE set
            env_backup = patch.dict(
                'os.environ', {k: v for k, v in __import__('os').environ.items() if k != 'RALPH_MODE'}, clear=True
            )
            with env_backup, patch('subprocess.run', return_value=fake_result):
                result = _detect_ralph_mode()
            assert result == 'self'

    def test_auto_detects_self_from_dynamic_dash_ralph(self):
        """Git remote containing 'dynamic-ralph' also triggers self mode."""
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='https://github.com/org/dynamic-ralph.git\n'
        )
        env = {k: v for k, v in __import__('os').environ.items() if k != 'RALPH_MODE'}
        with patch.dict('os.environ', env, clear=True), patch('subprocess.run', return_value=fake_result):
            from multi_agent.constants import _detect_ralph_mode

            result = _detect_ralph_mode()
        assert result == 'self'

    def test_defaults_to_third_party_for_other_repo(self):
        """Non-Ralph remote defaults to third-party."""
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout='git@github.com:encode/httpx.git\n')
        env = {k: v for k, v in __import__('os').environ.items() if k != 'RALPH_MODE'}
        with patch.dict('os.environ', env, clear=True), patch('subprocess.run', return_value=fake_result):
            from multi_agent.constants import _detect_ralph_mode

            result = _detect_ralph_mode()
        assert result == 'third-party'

    def test_defaults_to_third_party_without_git(self):
        """No git available defaults to third-party."""
        env = {k: v for k, v in __import__('os').environ.items() if k != 'RALPH_MODE'}
        with (
            patch.dict('os.environ', env, clear=True),
            patch('subprocess.run', side_effect=FileNotFoundError),
        ):
            from multi_agent.constants import _detect_ralph_mode

            result = _detect_ralph_mode()
        assert result == 'third-party'

    def test_explicit_env_var_overrides_auto_detect(self):
        """Explicit RALPH_MODE env var takes priority over auto-detection."""
        with patch.dict('os.environ', {'RALPH_MODE': 'self'}):
            from multi_agent.constants import _detect_ralph_mode

            result = _detect_ralph_mode()
        assert result == 'self'


# ---------------------------------------------------------------------------
# TestRalphModeValidation
# ---------------------------------------------------------------------------


class TestRalphModeValidation:
    """Test that invalid RALPH_MODE values are rejected."""

    def test_valid_self_mode(self):
        from multi_agent.constants import _VALID_MODES

        assert 'self' in _VALID_MODES

    def test_valid_third_party_mode(self):
        from multi_agent.constants import _VALID_MODES

        assert 'third-party' in _VALID_MODES

    def test_invalid_mode_not_in_valid_set(self):
        from multi_agent.constants import _VALID_MODES

        assert 'banana' not in _VALID_MODES

    def test_typo_mode_not_in_valid_set(self):
        from multi_agent.constants import _VALID_MODES

        assert 'thirdparty' not in _VALID_MODES


# ---------------------------------------------------------------------------
# TestBuildSystemPrompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    """Test build_system_prompt() behavior in different modes."""

    def test_self_mode_returns_base_instructions(self):
        """In self mode, system prompt is just BASE_AGENT_INSTRUCTIONS."""
        with patch('multi_agent.prompts.RALPH_MODE', 'self'):
            from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS, build_system_prompt

            result = build_system_prompt()
        assert result == BASE_AGENT_INSTRUCTIONS

    def test_third_party_mode_prepends_ralph_conventions(self):
        """In third-party mode with CLAUDE.md content, conventions are prepended."""
        with (
            patch('multi_agent.prompts.RALPH_MODE', 'third-party'),
            patch('multi_agent.prompts._RALPH_CLAUDE_MD_CONTENT', 'Test conventions'),
        ):
            from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS, build_system_prompt

            result = build_system_prompt()
        assert result.startswith('## Ralph Workflow Conventions')
        assert 'Test conventions' in result
        assert BASE_AGENT_INSTRUCTIONS in result

    def test_third_party_mode_without_content_returns_base(self):
        """In third-party mode without CLAUDE.md content, falls back to base."""
        with (
            patch('multi_agent.prompts.RALPH_MODE', 'third-party'),
            patch('multi_agent.prompts._RALPH_CLAUDE_MD_CONTENT', ''),
        ):
            from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS, build_system_prompt

            result = build_system_prompt()
        assert result == BASE_AGENT_INSTRUCTIONS


# ---------------------------------------------------------------------------
# TestDockerCommandRalphMode
# ---------------------------------------------------------------------------


class TestDockerCommandRalphMode:
    """Test that Docker commands propagate RALPH_MODE."""

    def test_claude_code_backend_propagates_ralph_mode(self):
        """ClaudeCodeBackend.build_docker_command includes RALPH_MODE env var."""
        with (
            patch('multi_agent.backends.claude_code.image_exists', return_value=True),
            patch('multi_agent.backends.claude_code.docker_sock_gid', return_value='999'),
            patch('multi_agent.backends.claude_code.get_git_author_identity', return_value=('Test', 'test@test.com')),
        ):
            from multi_agent.backends.claude_code import ClaudeCodeBackend

            backend = ClaudeCodeBackend()
            cmd = backend.build_docker_command(
                ['echo', 'test'],
                agent_id=1,
                workspace='/tmp/test',
            )
        # Find RALPH_MODE in the command
        ralph_mode_args = [arg for arg in cmd if 'RALPH_MODE=' in arg]
        assert len(ralph_mode_args) == 1
        assert ralph_mode_args[0].startswith('RALPH_MODE=')

    def test_run_agent_propagates_ralph_mode(self):
        """run_agent.py Docker command includes RALPH_MODE env var."""
        with (
            patch('bin.run_agent.docker_sock_gid', return_value='999'),
            patch('bin.run_agent.get_git_author_identity', return_value=('Test', 'test@test.com')),
        ):
            from bin.run_agent import build_interactive_docker_command

            cmd = build_interactive_docker_command(workspace='/tmp/test')
        ralph_mode_args = [arg for arg in cmd if 'RALPH_MODE=' in arg]
        assert len(ralph_mode_args) == 1


# ---------------------------------------------------------------------------
# TestDevFlag
# ---------------------------------------------------------------------------


class TestDevFlag:
    """Test --dev CLI flag in run_dynamic_ralph.py."""

    def test_dev_flag_accepted_by_parser(self):
        """The --dev flag is accepted by the argument parser."""

        # We can't easily test main() without side effects, so just verify
        # the parser accepts --dev by checking the module has the flag
        # (the parser is created inside main, so we test indirectly)
        import bin.run_dynamic_ralph as mod

        source = open(mod.__file__).read()
        assert "'--dev'" in source or '"--dev"' in source


# ---------------------------------------------------------------------------
# TestRalphSourceDir
# ---------------------------------------------------------------------------


class TestRalphSourceDir:
    """Test _ralph_source_dir() Docker build context resolution."""

    def test_self_mode_returns_cwd(self):
        """In self mode, _ralph_source_dir() returns Path.cwd()."""
        with patch('multi_agent.docker.RALPH_MODE', 'self'):
            from multi_agent.docker import _ralph_source_dir

            result = _ralph_source_dir()
        assert result == Path.cwd()

    def test_third_party_mode_returns_package_root(self, tmp_path):
        """In third-party mode, returns parent.parent of __file__ when docker/Dockerfile exists."""
        # Create a fake package layout: <root>/multi_agent/docker.py
        fake_root = tmp_path / 'ralph_root'
        (fake_root / 'docker').mkdir(parents=True)
        (fake_root / 'docker' / 'Dockerfile').touch()

        with (
            patch('multi_agent.docker.RALPH_MODE', 'third-party'),
            patch('multi_agent.docker.Path') as mock_path_cls,
        ):
            # Make Path(__file__).resolve().parent.parent return fake_root
            mock_file_path = mock_path_cls.return_value
            mock_resolved = mock_file_path.resolve.return_value
            mock_parent = mock_resolved.parent
            mock_parent.parent = fake_root

            # Make Path.cwd() still work (not used in this branch)
            mock_path_cls.cwd.return_value = Path.cwd()

            from multi_agent.docker import _ralph_source_dir

            result = _ralph_source_dir()
        assert result == fake_root

    def test_third_party_mode_raises_when_dockerfile_missing(self, tmp_path):
        """In third-party mode, raises FileNotFoundError when docker/Dockerfile is missing."""
        # Create a fake root WITHOUT docker/Dockerfile
        fake_root = tmp_path / 'no_docker'
        fake_root.mkdir()

        with (
            patch('multi_agent.docker.RALPH_MODE', 'third-party'),
            patch('multi_agent.docker.Path') as mock_path_cls,
        ):
            mock_file_path = mock_path_cls.return_value
            mock_resolved = mock_file_path.resolve.return_value
            mock_parent = mock_resolved.parent
            mock_parent.parent = fake_root

            from multi_agent.docker import _ralph_source_dir

            with pytest.raises(FileNotFoundError, match=r'Cannot locate docker/Dockerfile'):
                _ralph_source_dir()
