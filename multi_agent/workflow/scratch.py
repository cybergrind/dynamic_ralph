"""Scratch file management for Dynamic Ralph.

Two types of scratch files provide persistent memory across workflow steps:
- scratch.md (global) — shared across all stories/agents, protected by FileLock
- scratch_<story_id>.md (per-story) — scoped to a single story, no locking needed
"""

import os
import tempfile
from pathlib import Path

from multi_agent.filelock import FileLock


LOCK_TIMEOUT: int = 60

DEFAULT_SHARED_DIR = Path('.')

GLOBAL_SCRATCH = 'scratch.md'
GLOBAL_SCRATCH_LOCK = 'scratch.md.lock'


def _global_scratch_path(shared_dir: Path) -> Path:
    return shared_dir / GLOBAL_SCRATCH


def _global_lock_path(shared_dir: Path) -> str:
    return str(shared_dir / GLOBAL_SCRATCH_LOCK)


def _story_scratch_path(story_id: str, shared_dir: Path) -> Path:
    return shared_dir / f'scratch_{story_id}.md'


# ---------------------------------------------------------------------------
# Global scratch (FileLock-protected)
# ---------------------------------------------------------------------------


def read_global_scratch(shared_dir: Path = DEFAULT_SHARED_DIR) -> str:
    """Read scratch.md content. Return empty string if file doesn't exist."""
    path = _global_scratch_path(shared_dir)
    if not path.exists():
        return ''
    return path.read_text()


def write_global_scratch(content: str, shared_dir: Path = DEFAULT_SHARED_DIR) -> None:
    """Write to scratch.md with FileLock protection.

    Uses atomic write (temp file + rename) to avoid partial reads.
    """
    path = _global_scratch_path(shared_dir)
    lock_path = _global_lock_path(shared_dir)

    with FileLock(lock_path, timeout=LOCK_TIMEOUT):
        fd, tmp = tempfile.mkstemp(dir=str(shared_dir), suffix='.tmp')
        closed = False
        try:
            os.write(fd, content.encode())
            os.close(fd)
            closed = True
            os.rename(tmp, str(path))
        except BaseException:
            if not closed:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def append_global_scratch(message: str, shared_dir: Path = DEFAULT_SHARED_DIR) -> None:
    """Append a line to scratch.md with FileLock protection.

    Creates the file if it doesn't exist.
    """
    path = _global_scratch_path(shared_dir)
    lock_path = _global_lock_path(shared_dir)

    with FileLock(lock_path, timeout=LOCK_TIMEOUT):
        with open(path, 'a') as f:
            f.write(message + '\n')


# ---------------------------------------------------------------------------
# Per-story scratch (no locking — single writer per story)
# ---------------------------------------------------------------------------


def read_story_scratch(story_id: str, shared_dir: Path = DEFAULT_SHARED_DIR) -> str:
    """Read scratch_<story_id>.md content. Return empty string if file doesn't exist."""
    path = _story_scratch_path(story_id, shared_dir)
    if not path.exists():
        return ''
    return path.read_text()


def write_story_scratch(story_id: str, content: str, shared_dir: Path = DEFAULT_SHARED_DIR) -> None:
    """Write to scratch_<story_id>.md. No locking needed (single writer per story)."""
    path = _story_scratch_path(story_id, shared_dir)
    path.write_text(content)


def append_story_scratch(story_id: str, message: str, shared_dir: Path = DEFAULT_SHARED_DIR) -> None:
    """Append a line to story scratch file. Creates file if it doesn't exist."""
    path = _story_scratch_path(story_id, shared_dir)
    with open(path, 'a') as f:
        f.write(message + '\n')


def cleanup_story_scratch(story_id: str, shared_dir: Path = DEFAULT_SHARED_DIR) -> None:
    """Delete the per-story scratch file when the story completes."""
    path = _story_scratch_path(story_id, shared_dir)
    if path.exists():
        path.unlink()
