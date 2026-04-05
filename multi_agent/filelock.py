"""Exclusive file lock using fcntl."""

from __future__ import annotations

import fcntl
import time
from typing import IO


class FileLockTimeout(TimeoutError):
    """Raised when a FileLock cannot be acquired within the timeout period."""


class FileLock:
    """Exclusive file lock using fcntl with optional timeout.

    Args:
        path: Path to the lock file.
        timeout: Maximum seconds to wait for the lock. ``None`` means block
            indefinitely (the default for backward compatibility).
        poll_interval: Seconds between lock-acquisition retries (default 0.1).
    """

    def __init__(self, path: str, timeout: float | None = None, *, poll_interval: float = 0.1):
        self.path = path
        self.timeout = timeout
        self._poll_interval = poll_interval
        self.fd: IO[str] | None = None

    def __enter__(self):
        self.fd = open(self.path, 'w')
        if self.timeout is not None:
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.lockf(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fd.close()
                        raise FileLockTimeout(f'Could not acquire lock on {self.path} within {self.timeout}s')
                    time.sleep(self._poll_interval)
        else:
            fcntl.lockf(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        assert self.fd is not None, 'FileLock.__exit__ called without __enter__'
        fcntl.lockf(self.fd, fcntl.LOCK_UN)
        self.fd.close()
