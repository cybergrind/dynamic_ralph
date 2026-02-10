"""Shared prompts and instructions for Claude Code agents."""

BASE_AGENT_INSTRUCTIONS = """\
## First Steps
Read CLAUDE.md for project conventions. For detailed code patterns, read docs/code_guide.md.

## Test Execution
Run tests with: ./bin/run_agent_tests.sh <test_path> [pytest_args...]
First run needs BUILD=1. Subsequent runs reuse containers.
Example: ./bin/run_agent_tests.sh tests/test_api/test_dependencies.py
Subsequent test runs should complete in under 1 minute for small test files.
Do NOT use BUILD=1 unless you changed dependencies.

## Project Stack
- Python 3.13, uv package manager
- FastAPI, SQLAlchemy 2.0 async, Pydantic 2.x
- MySQL/MariaDB (main DB), TimescaleDB/PostgreSQL (audit log)
- Redis (cache), Kafka (events)
- pytest with pytest-asyncio (asyncio_mode=auto)

## Architecture Rules
- Layers: api/ → core/ → common/ (dependency flows downward only)
- core/ and common/ CANNOT import from api/
- Each domain in core/ follows: models.py, queries.py, actions.py, schemas.py, exceptions.py, rules.py

## Code Quality
- Format & lint: uv run pre-commit run -a
- Always run relevant tests after making changes, even if the task only asks for pre-commit
- When adding new functionality, write tests for it — even if not explicitly asked

## Common Workflows

### Add an API endpoint
1. Add route function in the appropriate router.py (async, type hints)
2. Add request *Validator and response *Serializer schemas in schemas.py
3. Use Annotated dependency aliases (DbSession, CurrentUser, etc.)
4. Write test in tests/test_api/ using auth_client fixture
5. Run tests, pre-commit

### Add a model column + migration
1. Add column to the model in models.py (use Mapped[T] type hints)
2. Generate migration: uv run alembic revision --autogenerate -m "description"
3. Review the generated migration file for correctness
4. Update any related serializer schemas
5. Run tests

### Write tests
- All tests are async (asyncio_mode=auto, no decorator needed)
- Use factories to create test data: await UserFactory(...), await ProfileFactory(team=team)
- Key fixtures: dbsession, client/auth_client, num_queries, adopt_dbsession, cache
- DB isolation: each test runs in a rolled-back transaction (force_rollback=True)
- For approx datetime matching: use dirty_equals.IsDatetime with approx/delta
- For time-sensitive tests: use freezegun

### Fix a flaky test
- Check for exact datetime comparisons → use dirty_equals.IsDatetime(approx=...)
- Check for ordering assumptions → add explicit ORDER BY or sort results
- Check for time-sensitive assertions → use freezegun to freeze time

### Write/edit documentation
- ALWAYS Read the file first before editing
- Use the Edit tool to append or modify sections (not Write for existing files)
- Keep sections concise (<100 lines per section)
- Run uv run pre-commit run -a after every change to verify formatting
- If pre-commit fails, read the error, fix the issue, and re-run

### Refactor across files
- Understand import rules: api/ → core/ → common/ (downward only)
- Extract helpers to the appropriate layer (common/ for shared utilities)

## Verification (do this after every change, not just before commits)
1. Run relevant tests: ./bin/run_agent_tests.sh <test_path>
2. Format & lint: uv run pre-commit run -a

## Anti-loop
If a command fails twice with the same error, try a different approach.

## Scope Discipline
Only modify files directly relevant to the task. Do not edit infrastructure or tooling files.

## Turn Efficiency
Read target files, plan edits, implement, verify. Minimize exploratory reads.
- When the task prompt provides specific file paths, line numbers, or commands, use them directly. Do NOT explore surrounding infrastructure (e.g., do not read shell scripts, Docker configs, or CI files when the exact command is given).
- When the task prompt provides factory names and locations (e.g., "TagFactory at tests/factories.py:509"), read just that file at that line — do not grep for factory patterns.
- Budget your turns: read target files → implement → verify. Each unnecessary read costs a turn.

## Read Strategy
- The system prompt already contains project conventions from CLAUDE.md. Do NOT re-read CLAUDE.md.
- docs/code_guide.md is the single reference for all code patterns. Read it ONCE if you need pattern details, then work from memory. Do not re-read sections.
- Before reading a new file, check if the information is already in your context from the task prompt, system prompt, or a previous read.
- Prefer reading one comprehensive reference file (docs/code_guide.md) over chaining reads through multiple source files to discover patterns.
- When you have the target file path from the task prompt, read it directly. Do not explore the directory tree first.
- Do NOT read test infrastructure files (run_agent_tests.sh, compose.test.yml, Dockerfiles) — the test command is already provided.

## Test Infra Recovery
If `./bin/run_agent_tests.sh` fails, retry once with `BUILD=1`. If it fails again, commit your changes and note the test failure.\
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
4. Validate the prd.json (check story sizes, ordering, criteria)

### Project Context
- Python 3.13, uv package manager
- FastAPI, SQLAlchemy 2.0 async, Pydantic 2.x
- Tests: ./bin/run_agent_tests.sh <test_path>
- Format & lint: uv run pre-commit run -a\
"""
