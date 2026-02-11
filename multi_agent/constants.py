"""Shared constants for ralph multi-agent scripts.

All values are configurable via environment variables for project-specific customization.
"""

from __future__ import annotations

import os
import subprocess
import sys


RALPH_IMAGE = os.environ.get('RALPH_IMAGE', 'ralph-agent:latest')
COMPOSE_FILE = os.environ.get('RALPH_COMPOSE_FILE', 'compose.test.yml')
ENV_FILE = os.environ.get('RALPH_ENV_FILE', '.env')
SERVICE = os.environ.get('RALPH_SERVICE', 'app')
INFRA_SERVICES = os.environ.get('RALPH_INFRA_SERVICES', 'mysql,redis').split(',')
GIT_EMAIL = os.environ.get('RALPH_GIT_EMAIL', 'claude-agent@dynamic-ralph.dev')
AGENT_BACKEND = os.environ.get('RALPH_AGENT_BACKEND', 'claude-code')
GIT_AUTHOR_NAME = os.environ.get('RALPH_GIT_AUTHOR_NAME')
GIT_AUTHOR_EMAIL = os.environ.get('RALPH_GIT_AUTHOR_EMAIL')

_FALLBACK_AUTHOR_NAME = 'Claude Agent'


def _read_git_config(key: str) -> str | None:
    """Read a value from host ``git config``, returning *None* on failure."""
    try:
        result = subprocess.run(
            ['git', 'config', key],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_git_author_identity() -> tuple[str, str]:
    """Resolve the git author name and email for agent containers.

    Priority (for each of name and email independently):
      1. ``RALPH_GIT_AUTHOR_NAME`` / ``RALPH_GIT_AUTHOR_EMAIL`` env var
      2. Host ``git config user.name`` / ``git config user.email``
      3. Fallback to ``'Claude Agent'`` / ``GIT_EMAIL`` with a warning
    """
    # --- name ---
    name = GIT_AUTHOR_NAME
    if not name:
        name = _read_git_config('user.name')
    if not name:
        name = _FALLBACK_AUTHOR_NAME
        print(
            'Warning: git author name not configured. Set RALPH_GIT_AUTHOR_NAME or run `git config user.name`.',
            file=sys.stderr,
        )

    # --- email ---
    email = GIT_AUTHOR_EMAIL
    if not email:
        email = _read_git_config('user.email')
    if not email:
        email = GIT_EMAIL
        print(
            'Warning: git author email not configured. Set RALPH_GIT_AUTHOR_EMAIL or run `git config user.email`.',
            file=sys.stderr,
        )

    return name, email
