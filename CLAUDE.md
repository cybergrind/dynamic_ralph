# Dynamic Ralph - Claude Code Project Guide

## Quick Start

Always run commands with `uv run`.

## Subsystems

- **Workflow engine** (`multi_agent/workflow/`) — sequential step executor; driven by `bin/run_dynamic_ralph.py`.
- **Multi-agent codex** (`multi_agent/orchestrate.py` + `parallel.py`, `codex_prompts.py`, `parsing.py`, `tally.py`, `extract.py`, `trace.py`) — parallel FRAME → PROPOSE → DEBATE → VOTE → DECIDE deliberation; user-facing entrypoints live in `skills/multi-agent*/orchestrate.py`.
- **Backends** (`multi_agent/backend.py`, `multi_agent/backends/claude_code.py`, `multi_agent/testing.py`) — `AgentBackend` protocol with prod (claude_code) and test (`TestingBackend`) implementations.
- **Coworker LLM** (`coworker_llm/`) — slash commands (`/ask-llm`, `/llm-write`, `/extract-chat`) that delegate to a configurable backend (default `opencode`, also `claude-code`). Selection via `--backend <name>` flag or `COWORKER_BACKEND` env. See `docs/coworker_llm_backends.md` and `scripts/coworker_smoke.py`.

## Project Structure

```
dynamic_ralph/
├── bin/
│   ├── run_dynamic_ralph.py # Main orchestrator (workflow entry point)
│   ├── run_agent.py         # Interactive agent runner in Docker
│   ├── run_retrospective.py # Retrospective analysis runner
│   ├── run_trace.py         # Trace-viewer TUI for run_ralph/ artifacts
│   └── cast_vote            # Structured vote submission (validates VoteOutput)
├── coworker_llm/            # Slash commands /ask-llm, /llm-write, /extract-chat
│   ├── backend.py           # CoworkerBackend protocol + get_backend registry
│   ├── ask_llm.py           # bulk file Q&A; routes to the configured backend
│   ├── llm_write.py         # boilerplate generation; routes to the configured backend
│   ├── extract_chat.py      # transcript summarization (local + --question via backend)
│   ├── backends/            # backend implementations (opencode, claude_code)
│   ├── LOWCOST.md           # token-preservation prompt loaded by slash commands
│   └── claude_commands/     # markdown installed to ~/.claude/commands/
├── scripts/
│   ├── install_coworker.sh      # `bash scripts/install_coworker.sh` to deploy
│   ├── coworker_smoke.py        # cross-backend smoke harness (canonical tasks)
│   └── verify_docker_auth.py    # verify Claude Code auth inside ralph-agent container
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
│   ├── orchestrate.py       # FRAME/PROPOSE/DEBATE/VOTE/DECIDE main loop
│   ├── codex_prompts.py     # Per-phase prompt composition
│   ├── parallel.py          # ThreadPoolExecutor-based parallel agent runner
│   ├── parsing.py           # Markdown → structured (Pydantic) output parser
│   ├── extract.py           # Validated extraction with retry-and-refine
│   ├── tally.py             # Vote tally, veto detection, decision building
│   ├── trace.py             # Thread-safe JSONL span recorder
│   ├── testing.py           # TestingBackend / AgentScript for tests without subprocesses
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
├── skills/                  # Slash-command skill bundles
│   ├── multi-agent/         # /multi-agent — full codex deliberation
│   │   ├── SKILL.md
│   │   └── orchestrate.py   # runner invoked by the slash command
│   └── multi-agent-fast/    # /multi-agent-fast — abridged codex flow
│       ├── SKILL.md
│       ├── fast_codex.md
│       └── orchestrate.py
├── docs/
│   ├── dynamic_ralph.md                  # Dynamic Ralph design spec
│   ├── ralph.md                          # Ralph pattern overview (historical)
│   ├── multi_agent_codex.md              # Multi-agent codex design + protocol
│   ├── identity_extraction.md            # Agent identity extraction notes
│   ├── harness_research.md               # Harness research notes
│   ├── howto_autonomous_code.md          # Autonomous-code how-to (short)
│   ├── howto_autonomous_code_generated.md# Autonomous-code how-to (generated)
│   ├── howto_run_claude_in_docker.md     # Running Claude Code inside Docker
│   ├── workflow_field_report.md          # Workflow engine field report
│   ├── coworker_llm_backends.md          # Coworker backend protocol, env vars, harness
│   └── plans/               # Roadmap / planning docs (00-index.md and friends)
├── tests/
│   ├── __init__.py                       # Package marker
│   ├── test_backend.py                   # Backend abstraction tests
│   ├── test_git_identity.py              # Git author identity tests
│   ├── test_log_paths.py                 # Log/diff path tests
│   ├── test_migration.py                 # Migration validation tests
│   ├── test_retrospective.py             # Retrospective runner tests
│   ├── test_run_agent.py                 # Agent runner tests
│   ├── test_run_directory.py             # Run directory generation tests
│   ├── test_summary_log.py               # Summary log tests
│   ├── test_workflow.py                  # Workflow module tests
│   ├── test_executor_bugs.py             # Workflow executor regressions
│   ├── test_orchestrate.py               # Codex orchestrate loop tests
│   ├── test_parallel.py                  # Parallel runner tests
│   ├── test_parsing.py                   # Parser tests
│   ├── test_tally.py                     # Tally / decision tests
│   ├── test_extract.py                   # Extract-and-refine tests
│   ├── test_codex_prompts.py             # Codex prompt-composition tests
│   ├── test_trace.py                     # Trace recorder tests
│   ├── test_trace_tui.py                 # Trace TUI tests
│   ├── test_testing_backend.py           # TestingBackend tests
│   ├── test_third_party_mode.py          # Third-party (non-claude_code) backend tests
│   ├── test_package.py                   # Package import / public API tests
│   ├── test_coworker_llm.py              # Coworker LLM unit tests
│   └── test_coworker_llm_integration.py  # Coworker LLM end-to-end tests
├── docker/
│   └── Dockerfile           # Agent container image
├── run_ralph/               # Per-run output artifacts (logs, traces, decisions); runtime-only
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

Use red/green TDD when it does make sense

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
infra, docs, gitignore, runner, scratch, retrospective, orchestrate, parallel,
parsing, tally, trace, extract, codex_prompts, testing, identity, coworker_llm,
skills, scripts, docker.

Start with a lowercase verb. No trailing period. No story IDs.
