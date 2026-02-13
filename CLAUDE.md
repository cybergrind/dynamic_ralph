# Dynamic Ralph - Claude Code Project Guide

## Quick Start

Always run commands with `uv run`.

## Project Structure

```
dynamic_ralph/
├── bin/
│   ├── run_dynamic_ralph.py # Main orchestrator (entry point)
│   ├── run_agent.py         # Interactive agent runner in Docker
│   └── run_retrospective.py # Retrospective analysis runner
├── multi_agent/             # Core package
│   ├── __init__.py          # Public re-exports
│   ├── backend.py           # Agent backend abstraction
│   ├── constants.py         # Configurable constants (env vars)
│   ├── compose.py           # Docker Compose wrappers
│   ├── docker.py            # Docker image helpers
│   ├── filelock.py          # File-based locking
│   ├── models.py            # PRD Pydantic models
│   ├── prd.py               # PRD file I/O
│   ├── prompts.py           # Agent instructions
│   ├── stream.py            # Event stream display
│   ├── backends/            # Backend implementations
│   │   ├── __init__.py
│   │   └── claude_code.py   # Claude Code backend
│   └── workflow/            # Workflow engine
│       ├── __init__.py
│       ├── editing.py       # Workflow edit validation/application
│       ├── executor.py      # Step execution engine
│       ├── models.py        # Workflow data models
│       ├── prompts.py       # Step prompt composition
│       ├── scratch.py       # Scratch file management
│       ├── state.py         # State persistence
│       └── steps.py         # Step type definitions
├── docs/
│   ├── dynamic_ralph.md     # Dynamic Ralph design spec
│   └── ralph.md             # Ralph pattern overview (historical)
├── tests/
│   ├── __init__.py          # Package marker
│   ├── test_backend.py      # Backend abstraction tests
│   ├── test_git_identity.py # Git author identity tests
│   ├── test_log_paths.py    # Log/diff path tests
│   ├── test_migration.py    # Migration validation tests
│   ├── test_retrospective.py # Retrospective runner tests
│   ├── test_run_agent.py    # Agent runner tests
│   ├── test_run_directory.py # Run directory generation tests
│   ├── test_summary_log.py  # Summary log tests
│   └── test_workflow.py     # Workflow module tests
├── docker/
│   └── Dockerfile           # Agent container image
└── pyproject.toml           # Project config (uv, ruff, pytest)
```

## Testing

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_workflow.py

# Run specific test class
uv run pytest tests/test_workflow.py::TestSteps
```

## Code Quality

```bash
# Format and lint
uv run pre-commit run -a
```

## Configuration

Constants in `multi_agent/constants.py` are configurable via environment variables:

- `RALPH_IMAGE` - Docker image name (default: `ralph-agent:latest`)
- `RALPH_COMPOSE_FILE` - Compose file path (default: `compose.test.yml`)
- `RALPH_ENV_FILE` - Env file path (default: `.env`)
- `RALPH_SERVICE` - Service name (default: `app`)
- `RALPH_INFRA_SERVICES` - Comma-separated list (default: `mysql,redis`)
- `RALPH_GIT_EMAIL` - Git committer email for commits (default: `claude-agent@dynamic-ralph.dev`)
- `RALPH_GIT_AUTHOR_NAME` - Git author name (default: host `git config user.name`)
- `RALPH_GIT_AUTHOR_EMAIL` - Git author email (default: host `git config user.email`)

## Commit Messages

This project uses component-scoped commit messages:

```
<component>: <lowercase verb phrase>
```

Components: orchestrator, executor, prompts, workflow, backend, models, tests,
infra, docs, gitignore, runner, scratch, retrospective.

Start with a lowercase verb. No trailing period. No story IDs.
