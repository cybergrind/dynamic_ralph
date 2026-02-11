"""Tests for get_git_author_identity() in multi_agent.constants."""

from __future__ import annotations

import importlib
import subprocess
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure author env vars are unset and constants module is reloaded cleanly."""
    monkeypatch.delenv('RALPH_GIT_AUTHOR_NAME', raising=False)
    monkeypatch.delenv('RALPH_GIT_AUTHOR_EMAIL', raising=False)
    import multi_agent.constants

    importlib.reload(multi_agent.constants)
    yield
    importlib.reload(multi_agent.constants)


def _make_git_config_side_effect(values: dict[str, str | None]):
    """Return a side-effect function for subprocess.run that mimics ``git config``."""

    def side_effect(cmd, **kwargs):
        key = cmd[-1]  # e.g. 'user.name' or 'user.email'
        value = values.get(key)
        if value is not None:
            return subprocess.CompletedProcess(cmd, 0, stdout=f'{value}\n', stderr='')
        return subprocess.CompletedProcess(cmd, 1, stdout='', stderr='')

    return side_effect


class TestGetGitAuthorIdentity:
    """Tests for the 3-tier priority resolution."""

    def test_env_var_overrides_git_config(self, monkeypatch):
        """Env vars win even when git config returns values."""
        monkeypatch.setenv('RALPH_GIT_AUTHOR_NAME', 'Env User')
        monkeypatch.setenv('RALPH_GIT_AUTHOR_EMAIL', 'env@example.com')
        import multi_agent.constants

        importlib.reload(multi_agent.constants)

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=_make_git_config_side_effect(
                {'user.name': 'Git User', 'user.email': 'git@example.com'}
            ),
        ):
            name, email = multi_agent.constants.get_git_author_identity()
        assert name == 'Env User'
        assert email == 'env@example.com'

    def test_git_config_used_when_env_vars_unset(self):
        """Host git config is used when RALPH_GIT_AUTHOR_* are not set."""
        import multi_agent.constants

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=_make_git_config_side_effect(
                {'user.name': 'Git User', 'user.email': 'git@example.com'}
            ),
        ):
            name, email = multi_agent.constants.get_git_author_identity()
        assert name == 'Git User'
        assert email == 'git@example.com'

    def test_fallback_with_warning_when_nothing_configured(self, capsys):
        """Falls back to Claude Agent / GIT_EMAIL and prints warnings."""
        import multi_agent.constants

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=_make_git_config_side_effect({}),
        ):
            name, email = multi_agent.constants.get_git_author_identity()

        assert name == 'Claude Agent'
        assert email == multi_agent.constants.GIT_EMAIL
        captured = capsys.readouterr()
        assert 'Warning: git author name not configured' in captured.err
        assert 'Warning: git author email not configured' in captured.err

    def test_partial_fallback_name_only(self, capsys):
        """If only email is available from git config, name falls back with warning."""
        import multi_agent.constants

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=_make_git_config_side_effect(
                {'user.email': 'git@example.com'}
            ),
        ):
            name, email = multi_agent.constants.get_git_author_identity()

        assert name == 'Claude Agent'
        assert email == 'git@example.com'
        captured = capsys.readouterr()
        assert 'Warning: git author name not configured' in captured.err
        assert 'Warning: git author email not configured' not in captured.err

    def test_partial_fallback_email_only(self, capsys):
        """If only name is available from git config, email falls back with warning."""
        import multi_agent.constants

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=_make_git_config_side_effect(
                {'user.name': 'Git User'}
            ),
        ):
            name, email = multi_agent.constants.get_git_author_identity()

        assert name == 'Git User'
        assert email == multi_agent.constants.GIT_EMAIL
        captured = capsys.readouterr()
        assert 'Warning: git author name not configured' not in captured.err
        assert 'Warning: git author email not configured' in captured.err

    def test_git_command_not_found(self, capsys):
        """Falls back gracefully when git is not installed."""
        import multi_agent.constants

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=FileNotFoundError('git not found'),
        ):
            name, email = multi_agent.constants.get_git_author_identity()

        assert name == 'Claude Agent'
        assert email == multi_agent.constants.GIT_EMAIL
        captured = capsys.readouterr()
        assert 'Warning: git author name not configured' in captured.err
        assert 'Warning: git author email not configured' in captured.err

    def test_git_command_timeout(self, capsys):
        """Falls back gracefully when git config times out."""
        import multi_agent.constants

        with patch.object(
            multi_agent.constants.subprocess,
            'run',
            side_effect=subprocess.TimeoutExpired('git', 5),
        ):
            name, email = multi_agent.constants.get_git_author_identity()

        assert name == 'Claude Agent'
        assert email == multi_agent.constants.GIT_EMAIL
