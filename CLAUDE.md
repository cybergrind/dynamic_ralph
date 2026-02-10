# Dynamic Ralph - Claude Code Project Guide

## Quick Start

Always run commands with `uv run`.

## Project Structure

```
dynamic_ralph/
├── bin/
│   └── run_dynamic_ralph.py # Main orchestrator (entry point)
├── multi_agent/             # Core package
│   ├── __init__.py          # Public re-exports
│   ├── constants.py         # Configurable constants (env vars)
│   ├── compose.py           # Docker Compose wrappers
│   ├── docker.py            # Docker image helpers
│   ├── filelock.py          # File-based locking
│   ├── models.py            # PRD Pydantic models
│   ├── prd.py               # PRD file I/O
│   ├── progress.py          # Progress tracking
│   ├── prompts.py           # Agent instructions
│   ├── stream.py            # Event stream display
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
│   └── ralph.md             # Ralph pattern overview
├── tests/
│   ├── test_workflow.py     # Workflow module tests
│   └── test_migration.py   # Migration validation tests
├── docker/
│   └── Dockerfile           # Agent container image
├── pyproject.toml           # Project config (uv, ruff, pytest)
└── prd.json                 # Example PRD
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
- `RALPH_GIT_EMAIL` - Git email for commits (default: `claude-agent@dynamic-ralph.dev`)
