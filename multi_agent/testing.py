"""Testing utilities for multi-agent orchestration.

Provides :class:`TestingBackend` and :class:`AgentScript` for writing
tests without spawning real subprocesses.
"""

from __future__ import annotations

from dataclasses import dataclass

from multi_agent.backend import AgentResult


@dataclass
class AgentScript:
    """Declarative agent response for testing — no subprocess needed."""

    response: str = ''
    structured_output: dict | None = None
    exit_code: int = 0
    timed_out: bool = False

    def to_result(self) -> AgentResult:
        """Convert to an :class:`AgentResult`."""
        return AgentResult(
            exit_code=self.exit_code,
            final_response=self.response,
            full_response=self.response,
            completion_status='end_turn' if self.exit_code == 0 else 'error',
            timed_out=self.timed_out,
            structured_output=self.structured_output,
        )


class TestingBackend:
    __test__ = False  # not a pytest test class
    """Backend that returns pre-built results without spawning processes.

    Usage::

        scripts = {
            'A': AgentScript(response='proposal A'),
            'B': AgentScript(response='proposal B'),
        }
        backend = TestingBackend(scripts)
    """

    def __init__(self, scripts: dict[str, AgentScript]) -> None:
        self.scripts = scripts

    def get_result(self, label: str) -> AgentResult:
        """Return the pre-built result for *label*, or raise KeyError."""
        return self.scripts[label].to_result()
