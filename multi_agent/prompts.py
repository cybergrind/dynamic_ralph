"""Shared prompts and instructions for Claude Code agents."""

from pathlib import Path

from multi_agent.constants import RALPH_INTERNAL_DOCS, RALPH_MODE


def _load_ralph_claude_md() -> str:
    """Load Ralph's CLAUDE.md content for third-party mode system prompt."""
    internal = Path(RALPH_INTERNAL_DOCS) / 'CLAUDE.md'
    if internal.is_file():
        return internal.read_text().strip()
    local = Path('CLAUDE.md')
    if local.is_file():
        return local.read_text().strip()
    return ''


_RALPH_CLAUDE_MD_CONTENT: str = _load_ralph_claude_md()


def build_system_prompt() -> str:
    """Build the full system prompt for agent invocations.

    Self mode: just BASE_AGENT_INSTRUCTIONS (unchanged behavior).
    Third-party mode: prepend Ralph's CLAUDE.md as workflow conventions.
    """
    if RALPH_MODE != 'third-party' or not _RALPH_CLAUDE_MD_CONTENT:
        return BASE_AGENT_INSTRUCTIONS
    return f'## Ralph Workflow Conventions\n\n{_RALPH_CLAUDE_MD_CONTENT}\n\n{BASE_AGENT_INSTRUCTIONS}'


BASE_AGENT_INSTRUCTIONS = """\
## First Steps
Read CLAUDE.md for project conventions. If a docs/ directory exists, check for code guides or architecture docs.

## Code Quality
- Format & lint: follow the project's linting configuration (e.g., `uv run pre-commit run -a`)
- Always run relevant tests after making changes
- When adding new functionality, write tests for it

## Verification (do this after every change)
1. Run relevant tests
2. Format & lint

## Anti-loop
If a command fails twice with the same error, try a different approach.

## Scope Discipline
Only modify files directly relevant to the task. Do not edit infrastructure or tooling files.

## Turn Efficiency
Read target files, plan edits, implement, verify. Minimize exploratory reads.
- When the task prompt provides specific file paths, line numbers, or commands, use them directly.
- Budget your turns: read target files → implement → verify. Each unnecessary read costs a turn.

## Read Strategy
- The system prompt already contains project conventions from CLAUDE.md. Do NOT re-read CLAUDE.md.
- Before reading a new file, check if the info is already in context from the task prompt or a previous read.\
"""
