"""Subprocess wrapper for the `opencode run` CLI."""

from __future__ import annotations

import subprocess


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


def run_opencode(prompt: str) -> str:
    result = subprocess.run(
        ['opencode', 'run', prompt],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OpenCodeError(
            f'opencode failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}',
            returncode=result.returncode,
        )
    return result.stdout
