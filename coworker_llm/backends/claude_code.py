"""Claude Code CLI backend.

Translates a :class:`CoworkerRequest` into a non-interactive ``claude -p``
invocation. Read access is granted by ``--add-dir`` for each parent
directory implied by ``reads`` (Claude reads via its own tools, so we
only need the directories on its allowlist). Write access is constrained
by running with ``cwd=writes_dir`` plus ``--allowedTools`` so Edit / Write
/ MultiEdit auto-approve in print mode without prompting.

Configuration env vars:

- ``COWORKER_CLAUDE_BIN`` — claude binary (default: ``claude``).
- ``COWORKER_CLAUDE_MODEL`` — model id (default: ``claude-haiku-4-5``).
- ``COWORKER_CLAUDE_UNRESTRICTED`` — when set to ``1``, pass
  ``--dangerously-skip-permissions`` instead of an explicit allowlist.
  Off by default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from coworker_llm.backend import CoworkerError, CoworkerRequest, CoworkerResult


DEFAULT_MODEL = 'claude-haiku-4-5'
DEFAULT_ALLOWED_TOOLS = ('Read', 'Write', 'Edit', 'MultiEdit', 'Glob', 'Grep')


class ClaudeCodeBackend:
    name = 'claude-code'

    def __init__(
        self,
        binary: str = 'claude',
        model: str = DEFAULT_MODEL,
        unrestricted: bool = False,
        allowed_tools: tuple[str, ...] = DEFAULT_ALLOWED_TOOLS,
    ) -> None:
        self._binary = binary
        self._model = model
        self._unrestricted = unrestricted
        self._allowed_tools = allowed_tools

    @classmethod
    def from_env(cls) -> 'ClaudeCodeBackend':
        return cls(
            binary=os.environ.get('COWORKER_CLAUDE_BIN', 'claude'),
            model=os.environ.get('COWORKER_CLAUDE_MODEL', DEFAULT_MODEL),
            unrestricted=os.environ.get('COWORKER_CLAUDE_UNRESTRICTED') == '1',
        )

    def is_available(self) -> bool:
        return shutil.which(self._binary) is not None

    def describe(self, request: CoworkerRequest) -> list[str]:
        return self._build_argv(request)

    def run(self, request: CoworkerRequest) -> CoworkerResult:
        cmd = self._build_argv(request)
        cwd = request.writes_dir
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise CoworkerError(
                f'{self.name} failed (exit {result.returncode}): {stderr}',
                returncode=result.returncode,
            )
        return CoworkerResult(stdout=result.stdout)

    def _build_argv(self, request: CoworkerRequest) -> list[str]:
        cmd: list[str] = [self._binary, '-p', request.prompt, '--model', self._model]
        if self._unrestricted:
            cmd.append('--dangerously-skip-permissions')
        else:
            cmd.append('--allowedTools')
            cmd.extend(self._allowed_tools)
        for d in self._allowed_dirs(request):
            cmd += ['--add-dir', d]
        return cmd

    @staticmethod
    def _allowed_dirs(request: CoworkerRequest) -> list[str]:
        dirs: list[str] = []
        if request.writes_dir is not None:
            dirs.append(request.writes_dir)
        for path in request.reads:
            parent = str(Path(path).parent)
            if parent and parent not in dirs:
                dirs.append(parent)
        return dirs
