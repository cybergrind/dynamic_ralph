"""Pluggable agent backends for coworker_llm.

The protocol is intent-level: a :class:`CoworkerRequest` declares what files the
agent must be allowed to read and what directory it may write into. Each
backend translates that intent into whatever CLI flags or tool registrations
its underlying agent needs.

Backend selection (in priority order):
    1. explicit ``name`` argument to :func:`get_backend`
    2. ``COWORKER_BACKEND`` environment variable
    3. default ``opencode``

CLI tools should call :func:`get_backend` once and pass the returned instance
to :meth:`CoworkerBackend.run`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CoworkerRequest:
    """Intent-level description of one agent invocation.

    Attributes
    ----------
    prompt:
        Free-form task description. May reference paths textually; backends
        ensure those paths are reachable via ``reads`` / ``writes_dir``.
    reads:
        Absolute paths the agent must be allowed to read.
    writes_dir:
        Directory the agent must be allowed to write into. ``None`` means
        the agent is expected to produce stdout only (no file output).
    expected_target:
        Informational; the path the caller expects the agent to create.
        Backends MAY use this to scope a write tool, but are not required
        to. Postcondition checks remain the caller's responsibility.
    """

    prompt: str
    reads: tuple[str, ...] = ()
    writes_dir: str | None = None
    expected_target: str | None = None


@dataclass(frozen=True)
class CoworkerResult:
    """Result of one backend invocation.

    ``extras`` is a free-form bag for backend-specific telemetry (model id,
    token counts, latency). CLI tools should treat it as opaque.
    """

    stdout: str
    extras: Mapping[str, str] = field(default_factory=dict)


class CoworkerError(RuntimeError):
    """Backend invocation failed.

    ``returncode`` is the underlying process exit code when applicable,
    otherwise ``None``.
    """

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


@runtime_checkable
class CoworkerBackend(Protocol):
    """Protocol every backend implements."""

    name: str

    def run(self, request: CoworkerRequest) -> CoworkerResult: ...

    def is_available(self) -> bool: ...


def list_backends() -> tuple[str, ...]:
    """Names of all built-in backends (in registration order)."""
    return tuple(_BACKEND_FACTORIES)


def get_backend(name: str | None = None) -> CoworkerBackend:
    """Return a backend instance.

    The ``name`` argument wins; otherwise ``COWORKER_BACKEND`` env wins;
    otherwise ``opencode``.
    """
    selected = name or os.environ.get('COWORKER_BACKEND') or 'opencode'
    factory = _BACKEND_FACTORIES.get(selected)
    if factory is None:
        available = ', '.join(list_backends())
        raise CoworkerError(f'unknown backend: {selected!r}; available: {available}')
    return factory()


def _opencode_factory() -> CoworkerBackend:
    from coworker_llm.backends.opencode import OpenCodeBackend
    return OpenCodeBackend.from_env()


def _claude_code_factory() -> CoworkerBackend:
    from coworker_llm.backends.claude_code import ClaudeCodeBackend
    return ClaudeCodeBackend.from_env()


_BACKEND_FACTORIES: dict[str, Callable[[], CoworkerBackend]] = {
    'opencode': _opencode_factory,
    'claude-code': _claude_code_factory,
}
