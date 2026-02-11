# Ralph: Autonomous Agent Development (Historical)

> **Note:** This document describes the origin and evolution of the Ralph pattern. For the current system design, see [Dynamic Ralph design spec](dynamic_ralph.md).

## What is Ralph?

Ralph is an autonomous AI agent loop that runs Claude Code repeatedly until all PRD items are complete. Each iteration is a fresh instance with clean context. Memory persists via git history and scratch files.

Based on [Geoffrey Huntley's Ralph pattern](https://ghuntley.com/ralph/) and adapted from [snarktank/ralph](https://github.com/snarktank/ralph).

### Core Philosophy

- **Fresh context per iteration** — each agent starts with a clean slate, no stale assumptions
- **Small stories** — each task fits in one context window, producing focused, reviewable changes
- **Learning loop** — discoveries are recorded in scratch files and step notes for future steps
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

### Key Differences from Original Ralph

| Aspect | Original Ralph | Dynamic Ralph |
|--------|---------------|--------------|
| Workflow | Single monolithic prompt per story | 10-step structured workflow per story |
| Step editing | None | Agents can add, split, skip, reorder steps at runtime |
| Environment | Runs directly on host | Runs in Docker container |
| Infrastructure | Assumes existing dev setup | Spins up isolated Docker Compose stacks |
| Launcher | `ralph.sh` (bash) | `bin/run_dynamic_ralph.py` (Python) |

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
