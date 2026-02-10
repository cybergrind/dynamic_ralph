# Ralph: Autonomous Agent Development

## What is Ralph?

Ralph is an autonomous AI agent loop that runs Claude Code repeatedly until all PRD items are complete. Each iteration is a fresh instance with clean context. Memory persists via git history, `progress.txt`, and `prd.json`.

Based on [Geoffrey Huntley's Ralph pattern](https://ghuntley.com/ralph/) and adapted from [snarktank/ralph](https://github.com/snarktank/ralph).

### Core Philosophy

- **Fresh context per iteration** — each agent starts with a clean slate, no stale assumptions
- **Small stories** — each task fits in one context window, producing focused, reviewable changes
- **Learning loop** — discoveries are recorded in `progress.txt` and `CLAUDE.md` for future iterations
- **Feedback loops** — tests, linting, and type checks verify each iteration before marking it complete

## The Original Ralph Approach

The original ralph repo provides:

- **`ralph.sh`** — a bash loop that spawns fresh AI instances (Amp or Claude Code), reads `prd.json`, and iterates until all stories pass
- **`prompt.md` / `CLAUDE.md`** — agent instructions: read the PRD, pick the next failing story, implement it, run quality checks, commit, update progress
- **Two-phase workflow**: first create a PRD interactively, then convert it to JSON, then run the autonomous loop

Key concepts from the original:
- Stories execute in priority order; earlier stories must not depend on later ones
- Each story must be completable in one context window
- Acceptance criteria must be verifiable (not vague)
- Stop condition: when all stories have `passes: true`, output `<promise>COMPLETE</promise>`

## Dynamic Ralph: Our Approach

Dynamic Ralph extends the original Ralph pattern with **structured, multi-step workflows**. Instead of giving each agent a single large prompt, Dynamic Ralph decomposes each story into focused, sequential steps (context gathering, planning, architecture, coding, testing, review, etc.).

See [Dynamic Ralph design spec](dynamic_ralph.md) for the full step-based workflow design.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Host Machine                                                │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Ralph Agent Container (Docker)                       │    │
│  │  - claude CLI (npx @anthropic-ai/claude-code)         │    │
│  │  - jq, git, docker CLI, docker compose                │    │
│  │  - python3, uv, make                                  │    │
│  │  - Mounts: /var/run/docker.sock (to control infra)    │    │
│  │  - Mounts: ./:/workspace (source code, bidirectional) │    │
│  │  - Mounts: ~/.claude:/root/.claude (OAuth credentials)│    │
│  │                                                        │    │
│  │  Spawns via docker compose (compose.test.yml):        │    │
│  │  ┌──────────────────────────────────────────────┐      │    │
│  │  │  Infrastructure Stack (unique project name)   │      │    │
│  │  │  - mysql, redis, etc.                         │      │    │
│  │  └──────────────────────────────────────────────┘      │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### Key Differences from Original Ralph

| Aspect | Original Ralph | Dynamic Ralph |
|--------|---------------|--------------|
| Workflow | Single monolithic prompt per story | 10-step structured workflow per story |
| Step editing | None | Agents can add, split, skip, reorder steps at runtime |
| Environment | Runs directly on host | Runs in Docker container |
| Infrastructure | Assumes existing dev setup | Spins up isolated Docker Compose stacks |
| Auth | API key in env | Mounts `~/.claude` for OAuth credentials |
| Launcher | `ralph.sh` (bash) | `bin/run_dynamic_ralph.py` (Python) |

### Execution

Dynamic Ralph is driven by a single orchestrator script:

```
bin/run_dynamic_ralph.py          # Orchestrator: one-shot, serial PRD, or parallel PRD
  └── Docker ralph container
        └── claude --dangerously-skip-permissions --print
              └── docker compose (compose.test.yml)  # Test infrastructure
```

### Execution Modes

#### One-shot mode

Execute a single task through the full 10-step workflow:

```bash
uv run python bin/run_dynamic_ralph.py "Add a logout button to the settings page"
```

#### PRD serial mode

Execute stories from a PRD file one at a time:

```bash
uv run python bin/run_dynamic_ralph.py --prd prd.json
```

#### PRD parallel mode

Execute stories concurrently with multiple agents:

```bash
uv run python bin/run_dynamic_ralph.py --prd prd.json --agents 3
```

## Quick Start

```bash
# 1. Build the Docker image (first time only)
uv run python bin/run_dynamic_ralph.py --build "dummy"

# 2. Run a one-shot task
uv run python bin/run_dynamic_ralph.py "Fix the N+1 query in profiles list endpoint"

# 3. Or run stories from a PRD
uv run python bin/run_dynamic_ralph.py --prd prd.json

# 4. Monitor progress
cat progress.txt
cat prd.json | python3 -c "import json,sys; [print(f'{s[\"id\"]}: {\"PASS\" if s[\"passes\"] else \"TODO\"} - {s[\"title\"]}') for s in json.load(sys.stdin)[\"userStories\"]]"
```

## Writing Good Stories

### Right-sized (one context window):
- Add a database column and migration
- Add an API endpoint with tests
- Update a service method with new logic
- Add a query filter to an existing endpoint

### Too big (split these):
- "Build the entire dashboard" → schema, queries, endpoints, tests
- "Add authentication" → schema, middleware, login endpoint, session handling
- "Refactor the API" → one story per endpoint or pattern

### Acceptance Criteria Rules
- Must be verifiable, not vague ("Tests pass" is good, "Works correctly" is bad)
- Always include "Tests pass" and "Lint passes (ruff check)"
- Be explicit: "Add `status` column to tasks table with default 'pending'"

### Dependency Ordering
1. Schema/database changes (migrations)
2. Backend logic (services, repositories)
3. API endpoints that use the backend
4. Integration tests that verify end-to-end

## Debugging

```bash
# See which stories are done
cat prd.json | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'{s[\"id\"]}: {\"PASS\" if s.get(\"passes\") else \"TODO\"} - {s[\"title\"]}') for s in d.get('userStories', d if isinstance(d, list) else [])]"

# See learnings from previous iterations
cat progress.txt

# Check git history
git log --oneline -10
```
