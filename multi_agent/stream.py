"""Stream event display for agent output.

Supports both the backend-agnostic :class:`~multi_agent.backend.AgentEvent`
(preferred) and raw ``dict`` events (legacy, for callers not yet migrated).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from multi_agent.backend import AgentEvent


def _truncate(text: str, max_len: int = 200) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + '...'


def display_agent_event(event: AgentEvent) -> None:
    """Print a human-readable summary of an :class:`AgentEvent` to stderr."""
    kind = event.kind

    if kind == 'system':
        print(f'[system] {event.text}', file=sys.stderr)
    elif kind == 'assistant':
        print(f'[assistant] {_truncate(event.text)}', file=sys.stderr)
    elif kind == 'tool_use':
        print(f'[tool_use] {_truncate(event.text)}', file=sys.stderr)
    elif kind == 'tool_result':
        print(f'[tool_result] {_truncate(event.text)}', file=sys.stderr)
    elif kind == 'result':
        print(f'[done] {event.text}', file=sys.stderr)
    elif kind == 'error':
        print(f'[error] {event.text}', file=sys.stderr)
    elif kind == 'raw':
        # Raw/unparseable lines are already printed by the backend; skip here
        pass
