"""Shared constants for ralph multi-agent scripts.

All values are configurable via environment variables for project-specific customization.
"""

from __future__ import annotations

import os
import subprocess
import sys


# --- execution mode ---
# Priority: explicit RALPH_MODE env var > auto-detect from git remote > third-party default.
_VALID_MODES = frozenset({'self', 'third-party'})


def _detect_ralph_mode() -> str:
    """Detect execution mode from environment or git remote URL.

    1. Explicit ``RALPH_MODE`` env var — highest priority.
    2. Git remote URL contains ``dynamic_ralph`` or ``dynamic-ralph`` — self mode.
    3. Default — ``third-party``.
    """
    explicit = os.environ.get('RALPH_MODE')
    if explicit:
        return explicit

    try:
        result = subprocess.run(
            ['git', 'config', '--get', 'remote.origin.url'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            url = result.stdout.strip().lower()
            if 'dynamic_ralph' in url or 'dynamic-ralph' in url:
                return 'self'
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return 'third-party'


RALPH_MODE: str = _detect_ralph_mode()

if RALPH_MODE not in _VALID_MODES:
    print(
        f'FATAL: RALPH_MODE={RALPH_MODE!r} is invalid. Must be one of: {sorted(_VALID_MODES)}',
        file=sys.stderr,
    )
    sys.exit(1)

# --- internal docs path (baked into Docker image at build time) ---
RALPH_INTERNAL_DOCS: str = '/opt/ralph'

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
