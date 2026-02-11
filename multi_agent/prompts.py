"""Shared prompts and instructions for Claude Code agents."""

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
