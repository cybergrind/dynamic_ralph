"""Docker image helpers for ralph agent containers."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from multi_agent.constants import RALPH_IMAGE, RALPH_MODE


DOCKERFILE_PATH = os.environ.get('RALPH_DOCKERFILE', 'docker/Dockerfile')

_PACKAGE_DATA_DIR = Path(__file__).resolve().parent / '_data'


def _ralph_source_dir() -> Path:
    """Return the root of the ralph source tree for Docker build context.

    Resolution order (third-party mode):
    1. Source checkout: ``Path(__file__).parent.parent`` has ``docker/Dockerfile``
    2. Package data:   ``multi_agent/_data/`` embedded in the wheel
    """
    if RALPH_MODE == 'self':
        return Path.cwd()
    # Source checkout (editable install or running from repo)
    candidate = Path(__file__).resolve().parent.parent
    if (candidate / 'docker' / 'Dockerfile').is_file():
        return candidate
    # Package data embedded by hatchling force-include
    if (_PACKAGE_DATA_DIR / 'docker' / 'Dockerfile').is_file():
        return _PACKAGE_DATA_DIR
    raise FileNotFoundError(
        f'Cannot locate docker/Dockerfile relative to {candidate} or in package data. '
        f'RALPH_MODE={RALPH_MODE}. '
        f'Ralph source tree not found.'
    )


def image_exists(image: str = RALPH_IMAGE) -> bool:
    result = subprocess.run(
        ['docker', 'image', 'inspect', image],
        capture_output=True,
    )
    return result.returncode == 0


def build_image(image: str = RALPH_IMAGE) -> None:
    source = _ralph_source_dir()
    # When building from package data, construct a full context with multi_agent/ included
    if source == _PACKAGE_DATA_DIR:
        context = _make_build_context(source)
    else:
        context = source
    dockerfile = str(context / DOCKERFILE_PATH)
    print(f'==> Building {image} (context={context})...')
    subprocess.run(
        ['docker', 'build', '-t', image, '-f', dockerfile, str(context)],
        check=True,
    )


def _make_build_context(data_dir: Path) -> Path:
    """Create a temporary Docker build context from package data + installed source.

    The Dockerfile expects ``multi_agent/`` in the build context (it copies it
    to ``/opt/ralph/multi_agent/``).  Package data only contains non-Python
    files, so we merge it with the installed ``multi_agent`` package.
    """
    ctx = Path(tempfile.mkdtemp(prefix='ralph-build-'))
    # Copy everything from the _data dir (Dockerfile, docs, CLAUDE.md, etc.)
    shutil.copytree(data_dir, ctx, dirs_exist_ok=True)
    # Copy the installed multi_agent package (excluding _data and __pycache__)
    pkg_src = Path(__file__).resolve().parent
    shutil.copytree(
        pkg_src,
        ctx / 'multi_agent',
        ignore=shutil.ignore_patterns('_data', '__pycache__'),
    )
    return ctx


def docker_sock_gid() -> str:
    """Return the GID of /var/run/docker.sock for --group-add."""
    return str(os.stat('/var/run/docker.sock').st_gid)


def host_claude_paths() -> tuple[Path, Path, Path]:
    """Resolve host paths for ``~/.claude``, ``~/.config/claude``, and ``~/.claude.json``.

    When running inside a container, ``Path.home()`` returns the container's
    home (e.g. ``/home/agent``), but Docker volume mounts are resolved by the
    daemon on the **host**.  ``~/.claude/full_path`` (written by Claude Code)
    contains the real host path.  If present, use it; otherwise fall back to
    ``Path.home()`` (correct when running directly on the host).
    """
    home = Path.home()
    claude_dir = home / '.claude'
    config_claude = home / '.config' / 'claude'
    claude_json = home / '.claude.json'

    full_path_file = claude_dir / 'full_path'
    if full_path_file.is_file():
        host_claude = Path(full_path_file.read_text().strip())
        # Derive ~/.config/claude from the same parent
        # /home/kpi/.claude → /home/kpi/.config/claude
        host_home = host_claude.parent
        claude_dir = host_claude
        config_claude = host_home / '.config' / 'claude'
        claude_json = host_home / '.claude.json'

    return claude_dir, config_claude, claude_json
