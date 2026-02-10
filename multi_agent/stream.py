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


def display_event(event: dict) -> None:
    """Print a human-readable summary of a raw stream-json event to stderr.

    This is the legacy interface kept for backward compatibility.  New code
    should use :func:`display_agent_event` with :class:`AgentEvent` objects.
    """
    etype = event.get('type', '')

    if etype == 'system':
        model = event.get('model', '')
        print(f'[system] session started (model={model})', file=sys.stderr)

    elif etype == 'assistant':
        message = event.get('message', {})
        for block in message.get('content', []):
            if block.get('type') == 'text':
                text = block.get('text', '')
                print(f'[assistant] {_truncate(text)}', file=sys.stderr)
            elif block.get('type') == 'tool_use':
                name = block.get('name', '?')
                tool_input = block.get('input', {})
                if name == 'Bash':
                    detail = tool_input.get('command', '')
                elif name in ('Read', 'Write'):
                    detail = tool_input.get('file_path', '')
                elif name == 'Edit':
                    detail = tool_input.get('file_path', '')
                elif name in ('Glob', 'Grep'):
                    detail = tool_input.get('pattern', '')
                elif name == 'Task':
                    detail = tool_input.get('description', '')
                else:
                    detail = str(tool_input)
                print(f'[tool_use] {name}: {_truncate(detail)}', file=sys.stderr)

    elif etype == 'user':
        tool_result = event.get('tool_use_result')
        if tool_result is None:
            return
        if isinstance(tool_result, str):
            print(f'[tool_result] {_truncate(tool_result)}', file=sys.stderr)
        elif isinstance(tool_result, dict):
            stdout = tool_result.get('stdout', '')
            stderr = tool_result.get('stderr', '')
            output = stdout or stderr
            if output:
                print(f'[tool_result] {_truncate(output)}', file=sys.stderr)
            elif tool_result.get('is_error'):
                print('[tool_result] (error)', file=sys.stderr)

    elif etype == 'result':
        subtype = event.get('subtype', '')
        cost = event.get('total_cost_usd')
        turns = event.get('num_turns', '?')
        cost_str = f', cost=${cost:.4f}' if cost else ''
        print(f'[done] {subtype} (turns={turns}{cost_str})', file=sys.stderr)
