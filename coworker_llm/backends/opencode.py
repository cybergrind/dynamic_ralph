"""opencode CLI backend.

Translates a :class:`CoworkerRequest` into ``opencode run <prompt> [--dir
<writes_dir>] [-f <read>]...`` and captures stdout. opencode's permission
system rejects writes outside its working directory and reads outside
allowed roots, so ``writes_dir`` maps to ``--dir`` and each ``reads`` entry
maps to ``-f``.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from coworker_llm.backend import CoworkerError, CoworkerRequest, CoworkerResult


class OpenCodeBackend:
    name = 'opencode'

    def __init__(self, binary: str = 'opencode') -> None:
        self._binary = binary

    @classmethod
    def from_env(cls) -> 'OpenCodeBackend':
        return cls(binary=os.environ.get('COWORKER_OPENCODE_BIN', 'opencode'))

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def describe(self, request: CoworkerRequest) -> list[str]:
        return self._build_argv(request)

    def run(self, request: CoworkerRequest) -> CoworkerResult:
        cmd = self._build_argv(request)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise CoworkerError(
                f'{self.name} failed (exit {result.returncode}): {stderr}',
                returncode=result.returncode,
            )
        return CoworkerResult(stdout=result.stdout)

    def _build_argv(self, request: CoworkerRequest) -> list[str]:
        cmd = [self._binary, 'run', request.prompt]
        if request.writes_dir is not None:
            cmd += ['--dir', request.writes_dir]
        for path in request.reads:
            cmd += ['-f', path]
        return cmd
