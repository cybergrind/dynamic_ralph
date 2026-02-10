"""Docker Compose wrappers for test infrastructure."""

import os
import subprocess
from pathlib import Path

from multi_agent.constants import ENV_FILE


def compose(*args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a docker compose command with --env-file."""
    cmd = ['docker', 'compose', '--env-file', ENV_FILE, *args]
    return subprocess.run(cmd, **kwargs)


def compose_bare(*args: str, **kwargs) -> subprocess.CompletedProcess:
    """Run a docker compose command without --env-file."""
    cmd = ['docker', 'compose', *args]
    return subprocess.run(cmd, **kwargs)


def geodb_volume() -> list[str]:
    """Volume mount for geodb."""
    workspace = os.environ.get('HOST_WORKSPACE', str(Path.cwd()))
    return ['-v', f'{workspace}/resources/geodb:/app/resources/geodb']
