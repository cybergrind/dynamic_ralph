"""Exclusive file lock using fcntl."""

import fcntl
import time


class FileLockTimeout(TimeoutError):
    """Raised when a FileLock cannot be acquired within the timeout period."""


class FileLock:
    """Exclusive file lock using fcntl with optional timeout.

    Args:
        path: Path to the lock file.
        timeout: Maximum seconds to wait for the lock. ``None`` means block
            indefinitely (the default for backward compatibility).
    """

    def __init__(self, path: str, timeout: int | None = None):
        self.path = path
        self.timeout = timeout
        self.fd = None

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
                    time.sleep(0.1)
        else:
            fcntl.lockf(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.lockf(self.fd, fcntl.LOCK_UN)
        self.fd.close()
