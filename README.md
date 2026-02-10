# Dynamic Ralph

A step-based multi-agent workflow orchestrator for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Dynamic Ralph decomposes user stories into focused, sequential steps and executes them via Claude Code agents. It supports three execution modes:

- **One-shot**: Single task, ephemeral state, full 10-step workflow
- **PRD serial**: Stories from a PRD file, executed one at a time
- **PRD parallel**: Multiple agents via git worktrees for concurrent story execution

## Requirements

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) package manager
- Docker (for containerized agent execution)
- Claude Code CLI (`npx @anthropic-ai/claude-code`)

## Setup

```bash
# Install dependencies
uv sync

# Build the Docker agent image
uv run python bin/run_dynamic_ralph.py --build "dummy"
```

## Usage

### One-shot mode

Execute a single task through the full workflow:

```bash
uv run python bin/run_dynamic_ralph.py "Add a logout button to the settings page"
```

### PRD serial mode

Execute stories from a PRD file one at a time:

```bash
uv run python bin/run_dynamic_ralph.py --prd prd.json
```

### PRD parallel mode

Execute stories concurrently with multiple agents:

```bash
uv run python bin/run_dynamic_ralph.py --prd prd.json --agents 3
```

### Resume a previous run

```bash
uv run python bin/run_dynamic_ralph.py --prd prd.json --resume
```

## Configuration

Dynamic Ralph uses environment variables for project-specific configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `RALPH_IMAGE` | `ralph-agent:latest` | Docker image for agent containers |
| `RALPH_COMPOSE_FILE` | `compose.test.yml` | Docker Compose file for infrastructure |
| `RALPH_ENV_FILE` | `.env` | Environment file for Docker Compose |
| `RALPH_SERVICE` | `app` | Main service name in Docker Compose |
| `RALPH_INFRA_SERVICES` | `mysql,redis` | Comma-separated infrastructure services |
| `RALPH_GIT_EMAIL` | `claude-agent@dynamic-ralph.dev` | Git email for agent commits |

## Workflow Steps

The default 10-step workflow:

1. **Context Gathering** - Explore codebase, understand relevant files
2. **Planning** - Design implementation approach
3. **Architecture** - Plan file changes and interfaces
4. **Test Architecture** - Design test strategy
5. **Coding** - Implement the changes
6. **Linting** - Format and lint (mandatory)
7. **Initial Testing** - Run tests, fix failures
8. **Review** - Self-review the implementation
9. **Prune Tests** - Remove redundant tests
10. **Final Review** - Final verification (mandatory)

Agents can dynamically edit the workflow (add, split, skip, reorder steps) during execution.

## Development

```bash
# Install dev dependencies
uv sync

# Run tests
uv run pytest

# Format and lint
uv run pre-commit run -a
```

## License

Apache-2.0
