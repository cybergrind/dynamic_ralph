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

PREPARE_SYSTEM_PROMPT = """\
## Ralph Preparation Session

You are helping prepare a PRD for ralph autonomous execution.

### Available Skills
1. **PRD Generator** — Read skills/prd/SKILL.md for instructions.
   Use this to create a structured PRD from a feature description.

2. **PRD-to-JSON Converter** — Read skills/ralph/SKILL.md for instructions.
   Use this to convert a PRD markdown file to prd.json.

### Workflow
1. Discuss the feature with the user
2. Create a PRD markdown file in tasks/prd-[feature-name].md
3. Convert it to prd.json using the converter skill
4. Validate the prd.json (check story sizes, ordering, criteria)\
"""
