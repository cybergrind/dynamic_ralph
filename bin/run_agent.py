#!/usr/bin/env python3
"""Run an interactive Claude Code session inside the ralph-agent container.

Mounts host credentials (~/.claude, ~/.config/claude), the current directory
as /workspace, and the Docker socket.  Uses ``os.execvp`` so the container
gets direct TTY access for a regular interactive Claude experience.

Extra arguments after ``--`` are forwarded to the ``claude`` CLI.
"""

import os
import sys
from pathlib import Path

from multi_agent.constants import GIT_EMAIL, RALPH_IMAGE, get_git_author_identity
from multi_agent.docker import build_image, docker_sock_gid, image_exists


def build_interactive_docker_command(
    *,
    image: str = RALPH_IMAGE,
    workspace: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build a ``docker run -it`` command for interactive Claude Code use."""
    if workspace is None:
        workspace = os.getcwd()

    author_name, author_email = get_git_author_identity()
    home = Path.home()
    claude_dir = home / '.claude'
    config_claude = home / '.config' / 'claude'

    cmd: list[str] = [
        'docker',
        'run',
        '-it',
        '--rm',
        '--group-add',
        docker_sock_gid(),
        '-e',
        'IS_SANDBOX=1',
        '-e',
        'UV_PROJECT_ENVIRONMENT=/tmp/venv',
        '-e',
        f'GIT_AUTHOR_NAME={author_name}',
        '-e',
        f'GIT_AUTHOR_EMAIL={author_email}',
        '-e',
        'GIT_COMMITTER_NAME=Claude Agent',
        '-e',
        f'GIT_COMMITTER_EMAIL={GIT_EMAIL}',
        '-v',
        '/var/run/docker.sock:/var/run/docker.sock',
        '-v',
        f'{workspace}:/workspace',
        '-v',
        '/workspace/.venv',
        '-v',
        f'{claude_dir}:/home/agent/.claude',
        '-v',
        f'{config_claude}:/home/agent/.config/claude',
        '-w',
        '/workspace',
        image,
        'claude',
        '--dangerously-skip-permissions',
    ]

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def main() -> None:
    # Ensure the Docker image is available
    if not image_exists():
        build_image()

    # Everything after '--' is forwarded to claude
    extra: list[str] = []
    if '--' in sys.argv:
        sep = sys.argv.index('--')
        extra = sys.argv[sep + 1 :]

    cmd = build_interactive_docker_command(extra_args=extra or None)
    os.execvp(cmd[0], cmd)


if __name__ == '__main__':
    main()
