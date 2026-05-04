"""Subprocess wrapper for the `opencode run` CLI.

opencode has a permission system that, by default, rejects writes outside its
working directory (CWD) and may reject reads of files outside allowed roots.
Pass `dir=` to set CWD via `--dir`, and `attach=` to attach input files via
`-f` so opencode can read them regardless of location.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


def run_opencode(
    prompt: str,
    *,
    dir: str | None = None,
    attach: Sequence[str] = (),
) -> str:
    cmd = ['opencode', 'run', prompt]
    if dir is not None:
        cmd += ['--dir', dir]
    for path in attach:
        cmd += ['-f', path]
    result = subprocess.run(
        cmd,
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
