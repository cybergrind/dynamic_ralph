#!/usr/bin/env python3
"""Run an interactive Claude Code session inside the ralph-agent container.

Mounts host credentials (~/.claude, ~/.config/claude), the current directory
as /workspace, and the Docker socket.  Uses ``os.execvp`` so the container
gets direct TTY access for a regular interactive Claude experience.

Extra arguments after ``--`` are forwarded to the ``claude`` CLI.
"""

import argparse
import os
import sys

from multi_agent.constants import GIT_EMAIL, RALPH_IMAGE, RALPH_MODE, get_git_author_identity
from multi_agent.docker import build_image, docker_sock_gid, host_claude_paths, image_exists


def build_interactive_docker_command(
    *,
    image: str = RALPH_IMAGE,
    workspace: str | None = None,
    extra_args: list[str] | None = None,
    prompt: str | None = None,
    print_mode: bool = False,
) -> list[str]:
    """Build a ``docker run`` command for Claude Code use.

    When *print_mode* is True the container runs non-interactively
    (``claude -p <prompt>``).  Otherwise a TTY is allocated for an
    interactive session, optionally pre-filled with *prompt*.
    """
    if workspace is None:
        workspace = os.getcwd()

    author_name, author_email = get_git_author_identity()
    claude_dir, config_claude, claude_json = host_claude_paths()

    cmd: list[str] = [
        'docker',
        'run',
    ]

    if print_mode:
        cmd.append('--rm')
    else:
        cmd.extend(['-it', '--rm'])

    cmd.extend(
        [
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
            '-e',
            f'RALPH_MODE={RALPH_MODE}',
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
            '-v',
            f'{claude_json}:/home/agent/.claude.json',
            '-w',
            '/workspace',
            image,
            'claude',
            '--add-dir',
            '/opt/ralph',
            '--dangerously-skip-permissions',
        ]
    )

    if print_mode and prompt:
        cmd.extend(['-p', prompt])
    elif prompt:
        cmd.append(prompt)

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse CLI arguments, returning parsed args and extra claude arguments.

    Arguments before ``--`` are parsed by argparse; everything after ``--``
    is forwarded verbatim to the ``claude`` CLI inside the container.
    """
    parser = argparse.ArgumentParser(
        description='Launch an interactive Claude Code session inside the ralph-agent container.',
    )
    parser.add_argument(
        '--build',
        action='store_true',
        help='Rebuild Docker image before launching',
    )
    parser.add_argument(
        '-p',
        '--print',
        dest='print_mode',
        action='store_true',
        help='Run in non-interactive print mode (claude -p)',
    )
    parser.add_argument(
        'prompt',
        nargs='?',
        default=None,
        help='Prompt to send to Claude (interactive by default, non-interactive with -p)',
    )

    if argv is None:
        argv = sys.argv[1:]

    extra: list[str] = []
    if '--' in argv:
        sep = argv.index('--')
        extra = argv[sep + 1 :]
        argv = argv[:sep]

    args = parser.parse_args(argv)
    return args, extra


def main() -> None:
    args, extra = parse_args()

    # Ensure the Docker image is available
    if args.build or not image_exists():
        build_image()

    cmd = build_interactive_docker_command(
        extra_args=extra or None,
        prompt=args.prompt,
        print_mode=args.print_mode,
    )
    os.execvp(cmd[0], cmd)


if __name__ == '__main__':
    main()
