"""Docker image helpers for ralph agent containers."""

import os
import subprocess
from pathlib import Path

from multi_agent.constants import RALPH_IMAGE


DOCKERFILE_PATH = os.environ.get('RALPH_DOCKERFILE', 'docker/Dockerfile')


def image_exists(image: str = RALPH_IMAGE) -> bool:
    result = subprocess.run(
        ['docker', 'image', 'inspect', image],
        capture_output=True,
    )
    return result.returncode == 0


def build_image(image: str = RALPH_IMAGE) -> None:
    print(f'==> Building {image}...')
    subprocess.run(
        ['docker', 'build', '-t', image, '-f', DOCKERFILE_PATH, '.'],
        check=True,
    )


def docker_sock_gid() -> str:
    """Return the GID of /var/run/docker.sock for --group-add."""
    return str(os.stat('/var/run/docker.sock').st_gid)


def host_claude_paths() -> tuple[Path, Path]:
    """Resolve host paths for ``~/.claude`` and ``~/.config/claude``.

    When running inside a container, ``Path.home()`` returns the container's
    home (e.g. ``/home/agent``), but Docker volume mounts are resolved by the
    daemon on the **host**.  ``~/.claude/full_path`` (written by Claude Code)
    contains the real host path.  If present, use it; otherwise fall back to
    ``Path.home()`` (correct when running directly on the host).
    """
    home = Path.home()
    claude_dir = home / '.claude'
    config_claude = home / '.config' / 'claude'

    full_path_file = claude_dir / 'full_path'
    if full_path_file.is_file():
        host_claude = Path(full_path_file.read_text().strip())
        # Derive ~/.config/claude from the same parent
        # /home/kpi/.claude → /home/kpi/.config/claude
        host_home = host_claude.parent
        claude_dir = host_claude
        config_claude = host_home / '.config' / 'claude'

    return claude_dir, config_claude
