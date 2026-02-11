"""Tests for bin/run_agent.py interactive Docker command builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bin.run_agent import build_interactive_docker_command


@pytest.fixture(autouse=True)
def _mock_docker_sock_gid():
    with patch('bin.run_agent.docker_sock_gid', return_value='999'):
        yield


@pytest.fixture(autouse=True)
def _mock_home(tmp_path: Path):
    with patch('bin.run_agent.Path.home', return_value=tmp_path):
        yield tmp_path


class TestBuildInteractiveDockerCommand:
    """Tests for build_interactive_docker_command."""

    def test_starts_with_docker_run_it(self):
        cmd = build_interactive_docker_command(workspace='/src')
        assert cmd[:4] == ['docker', 'run', '-it', '--rm']

    def test_workspace_mount(self):
        cmd = build_interactive_docker_command(workspace='/my/project')
        assert '/my/project:/workspace' in cmd

    def test_docker_socket_mount(self):
        cmd = build_interactive_docker_command(workspace='/src')
        assert '/var/run/docker.sock:/var/run/docker.sock' in cmd

    def test_credential_mounts(self, tmp_path: Path):
        cmd = build_interactive_docker_command(workspace='/src')
        assert f'{tmp_path / ".claude"}:/home/agent/.claude' in cmd
        assert f'{tmp_path / ".config" / "claude"}:/home/agent/.config/claude' in cmd

    def test_ends_with_claude_skip_permissions(self):
        cmd = build_interactive_docker_command(workspace='/src')
        # Find the image position, claude and flag follow it
        assert cmd[-2] == 'claude'
        assert cmd[-1] == '--dangerously-skip-permissions'

    def test_uses_default_image(self):
        cmd = build_interactive_docker_command(workspace='/src')
        # The image appears before 'claude'
        claude_idx = cmd.index('claude')
        assert cmd[claude_idx - 1] == 'ralph-agent:latest'

    def test_custom_image(self):
        cmd = build_interactive_docker_command(workspace='/src', image='my-img:v2')
        claude_idx = cmd.index('claude')
        assert cmd[claude_idx - 1] == 'my-img:v2'

    def test_extra_args_forwarded(self):
        cmd = build_interactive_docker_command(workspace='/src', extra_args=['--resume'])
        assert cmd[-1] == '--resume'
        assert cmd[-2] == '--dangerously-skip-permissions'

    def test_workspace_defaults_to_cwd(self):
        with patch('bin.run_agent.os.getcwd', return_value='/default/dir'):
            cmd = build_interactive_docker_command()
        assert '/default/dir:/workspace' in cmd

    @patch('bin.run_agent.image_exists', return_value=False)
    @patch('bin.run_agent.build_image')
    @patch('bin.run_agent.os.execvp')
    def test_main_builds_image_when_missing(self, mock_exec, mock_build, mock_exists):
        from bin.run_agent import main

        main()
        mock_build.assert_called_once()
        mock_exec.assert_called_once()

    @patch('bin.run_agent.image_exists', return_value=True)
    @patch('bin.run_agent.build_image')
    @patch('bin.run_agent.os.execvp')
    def test_main_skips_build_when_image_exists(self, mock_exec, mock_build, mock_exists):
        from bin.run_agent import main

        main()
        mock_build.assert_not_called()
        mock_exec.assert_called_once()

    @patch(
        'bin.run_agent.get_git_author_identity',
        return_value=('Host User', 'host@example.com'),
    )
    def test_git_author_uses_host_identity(self, mock_identity):
        cmd = build_interactive_docker_command(workspace='/src')
        assert 'GIT_AUTHOR_NAME=Host User' in cmd
        assert 'GIT_AUTHOR_EMAIL=host@example.com' in cmd

    @patch(
        'bin.run_agent.get_git_author_identity',
        return_value=('Host User', 'host@example.com'),
    )
    def test_git_committer_remains_claude_agent(self, mock_identity):
        cmd = build_interactive_docker_command(workspace='/src')
        assert 'GIT_COMMITTER_NAME=Claude Agent' in cmd
        from multi_agent.constants import GIT_EMAIL

        assert f'GIT_COMMITTER_EMAIL={GIT_EMAIL}' in cmd
