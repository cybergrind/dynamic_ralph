"""Placeholder for a direct Anthropic SDK backend.

Today this shells out to ``claude -p`` exactly like
:class:`coworker_llm.backends.claude_code.ClaudeCodeBackend`, but with its
own config namespace (``COWORKER_CLAUDE_API_*``) and a more capable default
model. The intent is to replace the implementation with a direct Anthropic
SDK call once we're willing to take on that dependency; the registry name
and env-var contract are stable now so callers can adopt
``COWORKER_BACKEND=claude-api`` without a future migration.

Configuration env vars:

- ``COWORKER_CLAUDE_API_BIN`` — claude binary (default: ``claude``).
- ``COWORKER_CLAUDE_API_MODEL`` — model id (default: ``claude-sonnet-4-6``).
- ``COWORKER_CLAUDE_API_UNRESTRICTED`` — when set to ``1``, pass
  ``--dangerously-skip-permissions`` instead of an explicit allowlist.
"""

from __future__ import annotations

import os

from coworker_llm.backends.claude_code import DEFAULT_ALLOWED_TOOLS, ClaudeCodeBackend


DEFAULT_MODEL = 'claude-sonnet-4-6'


class ClaudeApiBackend(ClaudeCodeBackend):
    name = 'claude-api'

    @classmethod
    def from_env(cls) -> 'ClaudeApiBackend':
        return cls(
            binary=os.environ.get('COWORKER_CLAUDE_API_BIN', 'claude'),
            model=os.environ.get('COWORKER_CLAUDE_API_MODEL', DEFAULT_MODEL),
            unrestricted=os.environ.get('COWORKER_CLAUDE_API_UNRESTRICTED') == '1',
            allowed_tools=DEFAULT_ALLOWED_TOOLS,
        )
