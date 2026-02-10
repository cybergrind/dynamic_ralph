# Dynamic Ralph: Structured Phases & Parallel Execution

## Introduction & Motivation

The current Ralph system (`docs/ralph.md`) gives each agent a single large prompt covering everything: planning, coding, testing, and review. The primary problem is **unstable quality** — when too much fits into one step, agents lose track of important details. For example, an agent may forget that Python must be run via `uv run` instead of `python3`, or ignore a specific acceptance criterion buried in context. The more an agent has to juggle, the more likely it is to drop something.

Dynamic Ralph addresses this by decomposing each story into **steps** — small, focused units of work with defined inputs, outputs, and exit criteria.

### Goals (in priority order)

1. **Stable, consistent quality.** Smaller steps mean shorter, more focused prompts. Each step carries only the context relevant to its task, so the agent is less likely to lose track of instructions, conventions, or requirements.

2. **Better PRDs with smaller steps.** Stories in `prd.json` evolve from monolithic "implement everything" blocks into sequences of well-defined steps. More steps, but each step is smaller and clearer — easier for agents to execute correctly and for humans to review.

3. **Dynamic workflow editing and recovery.** Agents can modify their remaining steps at runtime — add, split, skip, or reorder. Crucially, an agent can **edit a step's description and restart it**, which provides a recovery mechanism when the agent goes down a wrong path. Instead of failing the entire story, the agent corrects course and retries the step with an improved prompt.

4. **Parallel story execution.** Multiple agents work on different stories simultaneously, coordinated via a shared state file with file-level locking. A straightforward productivity boost.

This extends Phase 2 (Execute) of the existing Ralph workflow. Phase 1 (Prepare) remains unchanged.

### Design Principles

1. **Well-defined inputs and outputs.** Every step has explicitly defined inputs (what it receives from prior steps) and outputs (what it must produce for subsequent steps). The agent knows exactly what information it starts with and what it must deliver.

2. **Narrow scope of responsibility.** Each step does one thing. If a step requires the agent to juggle multiple concerns, it should be split. A step should never be both "explore" and "decide," or both "implement" and "review."

3. **Structured handoff.** Every step ends with a SUMMARY section that becomes the step's `notes`. These notes are passed to all subsequent steps in the same story, creating a reliable context chain independent of scratch files.

### Tradeoff: many small invocations vs. fewer large ones

This design deliberately uses many small agent invocations (10+ per story) instead of one large invocation. This increases token cost and adds container startup overhead. We accept this tradeoff because our experience with larger, monolithic agent invocations showed unstable quality — agents lose track of instructions, skip steps, and produce inconsistent results. Smaller scopes with clear definitions of done have proven more reliable. We may revisit this after evaluation and optimize (e.g., container reuse, collapsing read-only steps) if the overhead is too high.

## Concepts

### PRD

A product requirements document (`prd.json`) containing multiple stories. See the "PRD Format Changes" section below for fields added by Dynamic Ralph.

### Story

A user story from the PRD. Each story is assigned to one agent and executed as a sequence of steps. Stories are independent — agents working on different stories do not share branches or coordinate directly, though they can read the global scratch file.

### Step

A single unit of work within a story. Each step has a type, a description, and defined exit criteria. Steps execute sequentially within a story. Steps have well-defined inputs (context from prior steps' notes, scratch files, and the story description) and well-defined outputs (the work product plus a summary captured in `notes`). A step is the smallest unit the orchestrator schedules — one agent invocation per step.

### Workflow

The ordered list of steps for a story. The orchestrator generates a default workflow based on story type, but agents can modify remaining steps at runtime (see Step Editing).

### Workflow State File

A JSON file (`workflow_state.json`) that tracks the status of all stories and their steps. Protected by `FileLock` for concurrent access by multiple agents. The orchestrator reads this file to determine what to run next; agents write to it to report step completion or modify remaining steps.

### Scratch Files

Two types of scratch files provide persistent memory across steps:

- **`scratch.md`** (global) — Shared across all stories and all agents. Protected by `FileLock` for concurrent access. Contains cross-story findings and conventions (e.g., "All datetime columns use `func.now()` server default"). Agents should only write truly global observations here.

- **`scratch_<story_id>.md`** (per-story) — Scoped to a single story (e.g., `scratch_US-001.md`). No locking needed because steps within a story execute sequentially. Contains story-specific context: findings, decisions, plans, and lessons learned. Deleted or archived when the story completes.

Each agent receives the contents of both files in its prompt: the global scratch file plus its own story's scratch file. This keeps prompt size bounded — the global file stays small (only cross-cutting findings), and story files don't accumulate across stories.

The scratch files supplement the primary inter-step context mechanism — step `notes` from completed steps are passed directly to subsequent steps (see Step Execution Protocol). Agents use scratch files for detailed context that doesn't fit in a short summary, and for information that future steps or other stories may need.

### Step Editing

Agents can modify the remaining (not yet started) steps in their story's workflow. This is how agents adapt to unexpected findings — if a step reveals that the implementation needs an extra migration, the agent can insert a migration step. If a step fails because the approach was wrong, the agent can edit the step's description and restart it. The orchestrator enforces that only pending steps can be modified; completed and in-progress steps are immutable.

## PRD Format Changes

Dynamic Ralph extends the PRD format with additional fields per story. Existing fields (`id`, `title`, `description`, `acceptanceCriteria`, `priority`, `passes`, `notes`) remain unchanged.

### New Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `depends_on` | `string[]` | No (default: `[]`) | List of story IDs that must complete before this story can start |

### Example

```json
{
  "id": "US-003",
  "title": "Add profile status API endpoint",
  "description": "...",
  "acceptanceCriteria": ["..."],
  "depends_on": ["US-001", "US-002"]
}
```

The orchestrator validates the dependency graph at initialization using topological sort. Circular dependencies cause an immediate abort with an error message listing the cycle.

## Step Definitions

**Design principle:** Every step is a single agent invocation with a small, focused task and a clear definition of done. The agent receives only the context relevant to that step and knows exactly what "finished" looks like. No step should require the agent to juggle multiple concerns — if it does, it should be split into separate steps.

Each step definition includes a **Step Instructions** block — the template used to compose the agent's prompt. These instructions define exactly what the agent receives as input, what it must produce as output, and how it should work. Every step ends with a SUMMARY section that becomes the step's `notes` and is passed to all subsequent steps.

---

### 1. `context_gathering`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Explore the codebase, database schema, docs, and related code to build context |
| **Input** | Story description, acceptance criteria, scratch files |
| **Output** | Context summary: relevant files, DB models, existing patterns, related tests |
| **Exit criteria** | All areas relevant to the story are identified and documented in step notes |
| **Workflow editing** | No |

Pure exploration — no decisions, no planning. The agent reads the story, then systematically gathers everything a planner would need: relevant models, schemas, existing endpoints, test patterns, documentation. The output is a structured context summary that the next step (`planning`) will use as its input.

**Step Instructions:**

- **You receive:** Story description, acceptance criteria, global `scratch.md`, story `scratch_<story_id>.md`
- **You produce:** Context summary listing: relevant files with paths, DB models/schemas, existing patterns, related tests, current behavior
- **Key instructions:** Pure exploration — read code, grep for patterns, check models and schemas. Do NOT make decisions or plan. Write all findings to your story scratch file.
- End your response with a SUMMARY section (3-5 lines) that will be stored as step notes and passed to subsequent steps.

---

### 2. `planning`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Produce an implementation plan based on the gathered context |
| **Input** | Context summary from `context_gathering`, story acceptance criteria |
| **Output** | Implementation plan written to step notes |
| **Exit criteria** | Plan covers all acceptance criteria; files to modify are identified |
| **Workflow editing** | Yes — agent may add/split/reorder/skip steps based on story complexity |

The agent receives the context summary and focuses purely on decision-making: what to change, in what order, what approach to take. If the story is more complex than expected, the agent can split `coding` into multiple steps or add additional review phases. For simple stories (e.g., migration-only, documentation), the agent can skip unnecessary steps like `test_architecture` or `prune_tests`.

**Step Instructions:**

- **You receive:** Notes from `context_gathering`, story acceptance criteria, scratch files
- **You produce:** Implementation plan: what to change, in what order, which approach, which files
- **Key instructions:** Focus on decision-making based on gathered context. If the story is more complex than a single coding round, use workflow editing to split/add steps. For simple stories, skip unnecessary steps (e.g., skip `test_architecture` for migration-only work). Write the plan to your story scratch file.
- End your response with a SUMMARY section (3-5 lines).

---

### 3. `architecture`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Design code structure — models, schemas, queries, API changes |
| **Input** | Implementation plan from `planning`, existing code structure |
| **Output** | Architecture notes: new files, modified files, schema changes, migration needs |
| **Exit criteria** | All structural decisions documented; import dependencies verified |
| **Workflow editing** | Yes — may add migration steps, split coding phases |

The agent designs the technical approach: which domain modules to create or modify, what the data flow looks like, and how the change fits within the `api -> core -> common` layer boundaries.

**Step Instructions:**

- **You receive:** Notes from `context_gathering` + `planning`, scratch files
- **You produce:** Architecture notes: new/modified files, schema changes, migration needs, import dependencies, layer boundary compliance
- **Key instructions:** Design the technical structure. Verify it fits within `api -> core -> common` layering. If migration is needed, note it explicitly. May add/split coding steps via workflow editing.
- End your response with a SUMMARY section (3-5 lines).

---

### 4. `test_architecture`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Design the test strategy — what to test, which fixtures, edge cases |
| **Input** | Architecture from previous step, existing test patterns |
| **Output** | Test plan: test files, test classes, key test scenarios, fixtures needed |
| **Exit criteria** | Test plan covers all acceptance criteria; fixture requirements identified |
| **Workflow editing** | Yes — may adjust test strategy if gathered context reveals the architecture needs revision, or split testing phases for complex stories |

The agent plans the testing approach separately from implementation to ensure tests are designed independently, not reverse-engineered from the code.

**Step Instructions:**

- **You receive:** Notes from `architecture`, existing test patterns, scratch files
- **You produce:** Test plan: test files, test classes, key scenarios, fixtures needed, edge cases
- **Key instructions:** Design tests independently from implementation. Cover all acceptance criteria. Identify which fixtures exist and which need creation. Your test plan will be used by the `coding` step. May use workflow editing to adjust strategy if architecture needs revision.
- End your response with a SUMMARY section (3-5 lines).

---

### 5. `coding`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Implement the changes — write production code and tests |
| **Input** | Architecture notes, test plan, codebase |
| **Output** | Modified/created files committed to git |
| **Exit criteria** | All planned changes implemented; code compiles/imports without error |
| **Workflow editing** | Yes — may add steps (e.g., additional coding rounds for complex changes) |

The main implementation step. The agent writes production code and test code according to the plans from prior steps. If the implementation reveals unexpected complexity, the agent can insert additional steps.

**Step Instructions:**

- **You receive:** Notes from `architecture` + `test_architecture`, story scratch file
- **You produce:** Modified/created files committed to git
- **Key instructions:** Implement production code and tests according to the plans. Use `uv run` for all Python commands. Commit your changes. If you discover unexpected complexity, use workflow editing to add steps.
- End your response with a SUMMARY section (3-5 lines).

---

### 6. `linting`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Run formatters and lint checks, fix any issues |
| **Input** | Current codebase state |
| **Output** | Clean lint/format pass |
| **Exit criteria** | `uv run pre-commit run -a` passes |
| **Workflow editing** | No |

**Mandatory step — cannot be removed or skipped via workflow editing.** Runs the project's standard formatting and linting tools. The agent fixes any issues found rather than just reporting them.

> **Optimization note:** This step is a candidate for future optimization — the orchestrator could run `pre-commit` directly and only invoke an agent if it fails and fixes are needed. For now, it runs as a standard agent invocation for uniformity.

**Step Instructions:**

- **You receive:** Current codebase state
- **You produce:** Clean lint/format pass, fixes committed
- **Key instructions:** Run `uv run pre-commit run -a`. Fix any issues found. Re-run until clean. Commit fixes.
- End your response with a SUMMARY section (3-5 lines).

---

### 7. `initial_testing`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Run tests, identify and categorize any failures |
| **Input** | Current codebase state, test plan from `test_architecture` |
| **Output** | Test results; categorized failures (if any) |
| **Exit criteria** | All relevant tests executed; failures documented with root causes |
| **Workflow editing** | Yes — may add `coding` and `linting` steps to fix failures |

Runs the test suite for the affected area. If tests fail, the agent categorizes failures and can insert additional `coding` -> `linting` -> `initial_testing` cycles to fix them.

**Step Instructions:**

- **You receive:** Notes from `test_architecture`, current codebase
- **You produce:** Test results with pass/fail per test, categorized failures if any
- **Key instructions:** Run tests using `./bin/run_agent_tests.sh <test_path>`. If tests fail, categorize root causes and use workflow editing to add `coding -> linting -> initial_testing` fix cycle.
- End your response with a SUMMARY section (3-5 lines).

---

### 8. `review`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Self-review implementation against acceptance criteria, improve code quality |
| **Input** | Story acceptance criteria, current implementation, test results |
| **Output** | Review notes; any additional changes committed |
| **Exit criteria** | All acceptance criteria verified; no obvious issues remain |
| **Workflow editing** | Yes — may add steps for additional fixes or testing rounds |

The agent reviews its own work: for each acceptance criterion, it verifies the implementation and cites the specific file and line. It checks edge cases, error handling, and layer boundaries. Can trigger additional coding/testing cycles if issues are found.

**Step Instructions:**

- **You receive:** All prior step notes, acceptance criteria, test results, scratch files
- **You produce:** Review notes verifying each acceptance criterion with specific code references
- **Key instructions:** For each acceptance criterion, cite the specific file and line that implements it. If you cannot cite a specific location, the criterion is not met. Check error handling, edge cases, layer boundaries. If issues found, use workflow editing to add fix steps.
- End your response with a SUMMARY section (3-5 lines).

---

### 9. `prune_tests`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Remove redundant, overlapping, or low-value tests |
| **Input** | Current test suite for the story's area |
| **Output** | Pruned test files committed |
| **Exit criteria** | No redundant tests remain; coverage of acceptance criteria preserved |
| **Workflow editing** | No |

Agents tend to generate more tests than necessary. This step reviews the test suite and removes tests that duplicate coverage or test implementation details rather than behavior.

**Step Instructions:**

- **You receive:** Current test suite, all prior step notes
- **You produce:** Pruned test files committed
- **Key instructions:** Remove tests that duplicate coverage or test implementation details rather than behavior. Justify each removal. Do NOT remove tests that cover distinct edge cases or acceptance criteria.
- End your response with a SUMMARY section (3-5 lines).

---

### 10. `final_review`

| Aspect | Detail |
|--------|--------|
| **Purpose** | Final verification — all criteria met, clean commit, story complete |
| **Input** | Full story context, all previous step outputs |
| **Output** | Final commit; story marked as passing |
| **Exit criteria** | All acceptance criteria pass; tests pass; lint passes; commit is clean |
| **Workflow editing** | Yes — may add fix-up steps if final checks reveal issues, but cannot remove `final_review` itself |

**Mandatory step — cannot be removed or skipped via workflow editing.** The final gate before a story is marked complete. If issues are found, the agent can insert steps before `final_review` and loop back. `final_review` itself always remains as the last step.

**Step Instructions:**

- **You receive:** All prior step notes, full story context, scratch files
- **You produce:** Final verification that everything passes, clean final commit
- **Key instructions:** Run `uv run pre-commit run -a` and `./bin/run_agent_tests.sh <test_path>`. Verify all acceptance criteria are met. If issues found, add fix steps before this step via workflow editing. Create a clean final commit.
- End your response with a SUMMARY section (3-5 lines).

## Step State Machine

```
pending ──→ in_progress ──→ completed
                │
                ├──→ skipped    (with reason — only via workflow editing)
                │
                ├──→ failed     (with error — agent or system failure)
                │
                ├──→ cancelled  (external termination — timeout, dependency failure, user pause)
                │
                └──→ pending    (restart — agent edits description and retries)
```

**Transitions:**

| From | To | Trigger |
|------|----|---------
| `pending` | `in_progress` | Agent begins executing the step |
| `in_progress` | `completed` | Exit criteria met |
| `in_progress` | `failed` | Unrecoverable error or agent crash |
| `in_progress` | `cancelled` | External termination (timeout, dependency failure, orchestrator shutdown) |
| `in_progress` | `pending` | Agent triggers a restart — the step's description is edited and execution retries from scratch |
| `pending` | `skipped` | Workflow edit skips the step (with recorded reason) |

A `failed` or `cancelled` step does not automatically retry. The orchestrator decides whether to restart the story from the failed step or mark the story as failed.

**Restart:** When an agent realizes it went down the wrong path during a step, it can edit the step's description and reset it to `pending`. The agent must provide a `reason` explaining what went wrong and how the new description addresses it. The orchestrator will re-invoke the step with the updated description. The restart is recorded in history with the reason, old description, and new description.

**Restart limit:** Each step tracks a `restart_count`. A step can be restarted at most 3 times. After the limit is reached, the step is marked `failed` and the story follows the normal failure path. This prevents infinite restart loops — the 30-step maximum only caps total step count and does not cover restarts of the same step.

## Story State Machine

```
unclaimed ──→ in_progress ──→ completed
                  │
                  ├──→ failed
                  │
unclaimed ←── blocked
```

**Transitions:**

| From | To | Trigger |
|------|----|---------
| `unclaimed` | `in_progress` | Orchestrator assigns the story to an agent |
| `in_progress` | `completed` | All steps completed successfully (`final_review` passes) |
| `in_progress` | `failed` | A step fails with no recovery, or the 30-step limit is reached |
| `unclaimed` | `blocked` | A dependency story is marked `failed` |
| `blocked` | `unclaimed` | All dependency stories are now `completed` (re-evaluated each orchestrator iteration) |

**Status values:** `unclaimed`, `in_progress`, `completed`, `failed`, `blocked`

The orchestrator re-evaluates `blocked` stories on every loop iteration. If a previously failed dependency is retried and completed, its dependent stories automatically transition from `blocked` back to `unclaimed`.

## Workflow State File Schema

The shared state lives in `workflow_state.json` at the project root. All access is protected by `FileLock` (same mechanism used by `run_agent_tests.py`).

### Top-Level Schema

```json
{
  "version": 1,
  "created_at": "2025-01-15T10:00:00Z",
  "prd_file": "prd.json",
  "stories": {
    "<story_id>": { "...story workflow..." }
  }
}
```

### Story Workflow Schema

```json
{
  "story_id": "US-001",
  "title": "Add status field to profiles",
  "status": "in_progress",
  "agent_id": 1,
  "claimed_at": "2025-01-15T10:05:00Z",
  "completed_at": null,
  "depends_on": [],
  "steps": [
    { "...step..." }
  ],
  "history": [
    { "...history entry..." }
  ]
}
```

**Story status values:** `unclaimed`, `in_progress`, `completed`, `failed`, `blocked`

### Step Schema

```json
{
  "id": "step-001",
  "type": "context_gathering",
  "status": "completed",
  "description": "Explore codebase for profiles status field",
  "started_at": "2025-01-15T10:05:30Z",
  "completed_at": "2025-01-15T10:08:00Z",
  "git_sha_at_start": "a1b2c3d4",
  "notes": "Found: profiles/models.py (Profile model), profiles/schemas.py, test_profiles.py",
  "error": null,
  "skip_reason": null,
  "restart_count": 0,
  "cost_usd": 0.12,
  "input_tokens": 15000,
  "output_tokens": 3000,
  "log_file": "logs/US-001/step-001.jsonl"
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique step identifier, monotonically increasing within the story (e.g., `step-001`, `step-011`) |
| `type` | string | One of the 10 step types defined above |
| `status` | string | `pending`, `in_progress`, `completed`, `skipped`, `failed`, `cancelled` |
| `description` | string | Human-readable description of what this step does |
| `started_at` | string\|null | ISO 8601 timestamp when execution began |
| `completed_at` | string\|null | ISO 8601 timestamp when execution finished |
| `git_sha_at_start` | string\|null | Git commit SHA recorded when step transitions to `in_progress`. Used for clean rollback on failure or restart. |
| `notes` | string\|null | Agent-written SUMMARY output from executing the step. Passed to all subsequent steps. |
| `error` | string\|null | Error message if status is `failed` |
| `skip_reason` | string\|null | Reason if status is `skipped` |
| `restart_count` | integer | Number of times this step has been restarted (max 3) |
| `cost_usd` | number\|null | Token cost in USD for this step's agent invocation |
| `input_tokens` | integer\|null | Number of input tokens consumed |
| `output_tokens` | integer\|null | Number of output tokens produced |
| `log_file` | string\|null | Path to the full agent output log (e.g., `logs/US-001/step-001.jsonl`) |

**Step IDs:** New steps created via workflow editing use a monotonically increasing counter scoped to the story. If the default workflow ends at `step-010`, the first dynamically added step is `step-011`, then `step-012`, etc., regardless of insertion point. Position in the workflow is determined by array order, not by ID.

### History Entry Schema

```json
{
  "timestamp": "2025-01-15T10:12:00Z",
  "action": "workflow_edit",
  "agent_id": 1,
  "step_id": "step-005",
  "details": {
    "operation": "add_after",
    "target_step_id": "step-005",
    "reason": "Edge case in profile status validation needs a dedicated fix",
    "new_steps": [
      {
        "id": "step-011",
        "type": "coding",
        "description": "Fix failing test for edge case in profile status validation"
      }
    ]
  }
}
```

**History entry `details` fields** vary by operation but always include `operation` and `reason`.

**History action types:** `step_started`, `step_completed`, `step_failed`, `step_cancelled`, `step_skipped`, `workflow_edit`, `story_claimed`, `story_completed`, `story_failed`

## Step Editing Protocol

Agents can request workflow edits during execution. Not all steps allow editing — some are fixed.

### Which Steps Allow Editing

| Step | Workflow Editing |
|------|-----------------|
| `context_gathering` | No |
| `planning` | Yes |
| `architecture` | Yes |
| `test_architecture` | Yes |
| `coding` | Yes |
| `linting` | No |
| `initial_testing` | Yes |
| `review` | Yes |
| `prune_tests` | No |
| `final_review` | Yes (limited — cannot remove itself) |

### Allowed Operations

| Operation | Description |
|-----------|-------------|
| `add_after` | Insert one or more new steps after a specified step |
| `split` | Replace a pending step with multiple steps of the same or different types |
| `skip` | Mark a pending step as `skipped` with a reason |
| `reorder` | Change the order of pending steps |
| `edit_description` | Modify the description of a pending step |
| `restart` | Edit the description of the current `in_progress` step and reset it to `pending` for re-execution. Requires a `reason`. Step's `restart_count` is incremented. |

### Guardrails

1. **Mandatory steps cannot be skipped:** `linting` and `final_review` must always be present and executed.
2. **Only pending steps can be modified**, with one exception: an `in_progress` step can have its description edited as part of a `restart` (the step is reset to `pending` with the new description). Steps that are `completed`, `failed`, `skipped`, or `cancelled` are immutable. Note: `add_after` can reference any existing step as an insertion point regardless of its status — it modifies the workflow, not the target step itself.
3. **`final_review` must be last:** No step can be added after `final_review`. If `final_review` needs additional work, steps are inserted *before* it.
4. **Step IDs are unique:** New steps use a monotonically increasing counter scoped to the story (e.g., `step-011`, `step-012`).
5. **All edits are recorded:** Every edit operation creates a history entry with the full before/after diff.
6. **Agents can only edit their own story:** An agent can only edit steps in the story assigned to it. The orchestrator enforces this by only reading `workflow_edits/<assigned_story_id>.json` for each agent.
7. **Maximum step count:** A workflow cannot exceed 30 steps. If an edit would exceed this limit, the entire edit file is rejected.
8. **Restart limit:** A single step cannot be restarted more than 3 times. Further restart requests are rejected.
9. **Edit validation is atomic:** All operations in an edit file are validated before any are applied. If any operation fails validation, the entire edit file is rejected, an error is logged, and no changes are made to the workflow state.
10. **Edit rejection feedback:** When an edit is rejected, the orchestrator writes the rejection reason to the story's scratch file (`scratch_<story_id>.md`) so the next step's agent has context.
11. **Reorder validation:** The `new_order` array must contain exactly the set of all pending step IDs — no additions, no omissions. `final_review` must remain the last element. Any mismatch causes rejection.

## Atomic Read-Modify-Write

All workflow state modifications follow this protocol. The `workflow_state.json` file is only written by the orchestrator — agents never write to it directly. This means lock contention is minimal even with multiple parallel agents.

```python
from filelock import FileLock

lock = FileLock("workflow_state.json.lock", timeout=60)

with lock:
    # 1. Read current state
    state = json.loads(Path("workflow_state.json").read_text())

    # 2. Validate edit (check guardrails)
    validate_edit(state, story_id, edit_operation)

    # 3. Apply edit
    apply_edit(state, story_id, edit_operation)

    # 4. Append history entry
    state["stories"][story_id]["history"].append(history_entry)

    # 5. Write back atomically (temp file + rename)
    tmp = Path("workflow_state.json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(Path("workflow_state.json"))
```

The lock has a 60-second timeout. If the lock cannot be acquired within this period, the operation fails with a clear error. This prevents indefinite blocking if a process hangs.

The lock file (`workflow_state.json.lock`) is separate from the state file, using OS-level advisory locks (`fcntl`) that are automatically released on process death.

## Step Execution Protocol

How the orchestrator runs each step:

1. **Read state** — find the next `pending` step for the assigned story
2. **Record git SHA** — save the current `HEAD` commit as `git_sha_at_start` on the step
3. **Mark `in_progress`** — update `workflow_state.json` with status and `started_at` timestamp
4. **Build prompt** — compose the agent's prompt from:
   - Step-specific instructions (what to do, exit criteria, prohibited actions)
   - Story description and acceptance criteria
   - Current step's `description` field (may be customized by workflow edits)
   - Notes from all completed steps in the same story (the structured context chain)
   - Contents of `scratch.md` (global shared context)
   - Contents of `scratch_<story_id>.md` (story-specific context)
5. **Launch agent** — invoke Claude Code with the composed prompt, with a per-step-type timeout
6. **Collect output** — when the agent exits (or is killed on timeout), capture its result
7. **Process workflow edits** — check for `workflow_edits/<story_id>.json`, validate and apply edits to `workflow_state.json`
8. **Update state** — write the agent's output to the step's `notes` field, set status to `completed` (or `failed`/`cancelled`), record `completed_at` timestamp and token usage

**Important:** Workflow edits are processed (step 7) *before* the step is marked complete (step 8). This ensures that if the orchestrator crashes between these operations, the step remains `in_progress` and the full sequence (including edits) replays on restart.

The agent reads and updates scratch files during execution. The `notes` field is the primary inter-step communication mechanism — it carries a structured summary from each step to all subsequent steps. Scratch files hold supplementary detailed context.

**Per-step timeouts:**

| Step type | Default timeout |
|-----------|----------------|
| `context_gathering` | 15 min |
| `planning` | 10 min |
| `architecture` | 10 min |
| `test_architecture` | 10 min |
| `coding` | 30 min |
| `linting` | 5 min |
| `initial_testing` | 20 min |
| `review` | 10 min |
| `prune_tests` | 10 min |
| `final_review` | 15 min |

When a timeout fires, the orchestrator kills the agent container and follows the failure handling protocol. The step is marked `cancelled` (not `failed`) with a timeout-specific error.

**Logging:** Each step's full agent output is captured to `logs/<story_id>/<step_id>.jsonl` in stream-json format. The path is recorded in the step's `log_file` field for later debugging.

## Failure Handling

When an agent exits with a non-zero code (or is killed by timeout), the orchestrator:

1. Checks for and discards any `workflow_edits/<story_id>.json` file — edits from failed steps are not applied
2. Captures the full diff including untracked files: `git add -A && git diff HEAD` — saves to `failures/<story_id>-<step_id>.diff`
3. Resets the worktree to the pre-step state: `git reset --hard <git_sha_at_start>` — this precisely restores the state before the step began, removing both committed and uncommitted changes
4. Marks the step as `failed` (or `cancelled` for timeouts) in `workflow_state.json` with the error message
5. Marks the story as `failed`
6. Logs the failure to `scratch.md` (global) so other agents are aware

The diff is saved before resetting so that a human (or a future retry) can inspect what the agent attempted. The `git_sha_at_start` ensures precise rollback — even if the agent made commits during the step, all changes are reverted.

The orchestrator does not automatically retry failed stories — that is a manual decision (see Orchestrator Crash Recovery).

**Restart handling:** When a step is restarted (via the `restart` workflow edit operation), the orchestrator follows steps 2-3 above (save diff, reset to `git_sha_at_start`) before re-invoking the step. The diff is saved to `restarts/<story_id>-<step_id>-<attempt>.diff` for debugging. The step's `restart_count` is incremented.

## Workflow Edit Requests

Agents do **not** write to `workflow_state.json` directly. Instead, if an agent wants to edit the workflow (add steps, restart, skip, etc.), it writes an edit request file:

```
workflow_edits/<story_id>.json
```

The file should be written atomically (write to a temp file, then rename) to prevent partial reads.

The file contains one or more edit operations:

```json
[
  {
    "operation": "add_after",
    "target_step_id": "step-007",
    "reason": "2 tests failed — need fix cycle before review",
    "new_steps": [
      { "type": "coding", "description": "Fix transition validation and auth check" },
      { "type": "linting", "description": "Re-lint after fixes" },
      { "type": "initial_testing", "description": "Re-run tests after fixes" }
    ]
  }
]
```

When the agent exits, the orchestrator:

1. Checks the agent's exit code
2. If the step **failed**: discards the edit file (moves to `workflow_edits/failed/` for debugging) — edits from failed steps are not applied
3. If the step **succeeded**: validates all operations against guardrails (atomic — all-or-nothing)
4. If validation passes: applies edits to `workflow_state.json` with `FileLock`, records history entries, deletes the edit file
5. If validation fails: rejects the entire edit file, logs the rejection reason, and writes it to `scratch_<story_id>.md` so the next step's agent has context
6. Marks the step as completed

The orchestrator only reads `workflow_edits/<assigned_story_id>.json` for each agent — it ignores edit files for other stories. This enforces the "agents can only edit their own story" guardrail.

## Parallel Story Execution

**Key constraint:** Steps within a story are always executed sequentially — each step depends on the output of the previous one. Parallelism happens at the **story level**: multiple agents work on different stories simultaneously. Not all stories can be parallelized — stories that depend on each other (via `depends_on`) must run in order.

### Story Assignment

The orchestrator (`run_ralph.py`) is responsible for assigning stories to agents. Agents do not choose stories themselves — they receive a story ID when launched.

```python
# Orchestrator picks the next story and assigns it
with FileLock("workflow_state.json.lock", timeout=60):
    state = load_state()

    story = find_assignable_story(state)
    if story is None:
        break  # No work available

    story["status"] = "in_progress"
    story["agent_id"] = agent_id
    story["claimed_at"] = utcnow()

    if not story["steps"]:
        story["steps"] = create_default_workflow()

    save_state(state)  # atomic rename

# Launch agent with the assigned story
run_agent(agent_id=agent_id, story_id=story["story_id"])
```

A story is assignable when:
- `status` is `unclaimed`
- All stories in its `depends_on` list have `status == "completed"`

**Dependency validation:** At initialization, the orchestrator performs a topological sort of the dependency graph. Circular dependencies cause an immediate abort with an error message listing the cycle.

### Workspace Isolation

Each agent needs its own copy of the codebase to avoid git conflicts and uncommitted file collisions. We use `git worktree` to give each agent an isolated working tree while sharing the same repo.

**Isolated per agent (via worktree):**
- Source code — each agent works on its own branch in its own directory
- Git state — commits, staging area, uncommitted changes are independent

**Shared across all agents (in the main repo root):**
- `workflow_state.json` — story/step state (accessed via `FileLock`)
- `scratch.md` — global shared context (accessed via `FileLock`)
- `scratch_<story_id>.md` — per-story context files (single writer, no lock needed)
- `workflow_edits/` — edit request files from agents
- `logs/` — per-step agent output logs

The orchestrator sets up worktrees when assigning stories:

```bash
# Create a worktree for agent 1 working on US-001
git worktree add worktrees/agent-1 -b ralph/US-001 master
```

Note the explicit `master` base — all worktrees branch from the main branch.

Agents access shared files via the `RALPH_SHARED_DIR` environment variable, which points to the main repo root.

### Merge Strategy

When a story completes, the orchestrator integrates the changes using a rebase-then-squash-merge strategy:

1. **Rebase** the story branch onto the current base branch: `git rebase master ralph/US-001`
2. **If rebase succeeds:** squash merge into the base branch and remove the worktree
   ```bash
   git merge --squash ralph/US-001
   git commit -m "feat(ralph): US-001 - Add status field to profiles"
   git worktree remove worktrees/agent-1
   ```
3. **If rebase conflicts:** the orchestrator inserts a `rebase_resolve` step before `final_review` in the story's workflow, and re-invokes the agent to resolve conflicts. After resolution, `final_review` re-runs to verify everything still passes.

This strategy ensures that:
- The base branch never has conflicts — all conflicts are resolved on the story branch
- Alembic migration chain conflicts (two stories adding migrations from the same base) are detected and resolved during rebase
- Each merged story is a clean single commit on the base branch

### Infrastructure Isolation

Each agent already gets a unique `COMPOSE_PROJECT_NAME=ralph_agent_<id>` (set by `run_agent.py`). This means each agent's test infrastructure (MySQL, Redis, Kafka, TimescaleDB) runs in a separate Docker Compose project with no port or volume conflicts.

### Inter-Story Dependencies

Stories can declare dependencies via the `depends_on` field in the PRD:

```json
{
  "id": "US-003",
  "title": "Add profile status API endpoint",
  "depends_on": ["US-001", "US-002"]
}
```

The orchestrator only assigns stories whose dependencies have all completed.

**Failure propagation:** When a story is marked `failed`, the orchestrator scans all other stories and marks any that depend on it (directly or transitively) as `blocked`.

**Re-evaluation:** The orchestrator re-evaluates `blocked` stories on every loop iteration. If a previously failed dependency is retried and completed, dependent stories automatically transition from `blocked` back to `unclaimed`.

The orchestrator's loop exits when all stories are `completed`, `failed`, or `blocked`.

### Orchestrator Crash Recovery

On startup, the orchestrator performs a reconciliation phase:

1. **Scan for orphaned stories** — any story with `status: in_progress` whose agent container is not running
2. **For each orphaned story:** mark the current `in_progress` step as `failed` with error "orchestrator restart — agent not found"
3. **Save partial work** — if the worktree has uncommitted changes, save a diff and reset to `git_sha_at_start`
4. **Resume** — the story remains `in_progress` and the orchestrator picks up execution from the next `pending` step. If no pending steps remain, the story is marked `failed`.

This ensures the system recovers gracefully from power loss, OOM kills, or manual orchestrator restarts.

### Orchestrator Loop

`run_ralph.py` runs a loop: find assignable stories, spawn agents (up to N in parallel), wait, repeat.

```
run_ralph.py --agents 3 --prd prd.json
│
├── Validates dependency graph (topological sort, detects cycles)
├── Initializes workflow_state.json from prd.json (if not exists)
├── Runs startup reconciliation (recovers orphaned stories)
│
└── Loop:
    ├── Re-evaluate blocked stories (unblock if dependencies met)
    ├── Find assignable stories (unclaimed, dependencies met)
    ├── Assign up to N stories to available agent slots
    │   ├── Agent 1 ──→ run_agent.py --agent-id 1 --story US-001
    │   ├── Agent 2 ──→ run_agent.py --agent-id 2 --story US-003
    │   └── Agent 3 ──→ run_agent.py --agent-id 3 --story US-004
    ├── Wait for any agent to finish a step
    ├── Process step result (edits, completion, failure)
    ├── If story complete: rebase and merge
    └── Repeat until all stories are completed, failed, or blocked
```

### Structured Logging

Each agent's full output is captured to `logs/<story_id>/<step_id>.jsonl` in stream-json format. The orchestrator itself logs structured JSON to stderr:

```json
{"ts": "2025-01-15T10:05:00Z", "agent_id": 1, "story_id": "US-001", "step_id": "step-001", "event": "step_started"}
```

This enables filtering by agent, story, or step when debugging parallel execution.

## One-Shot Mode

An agent can also be launched without a PRD or story — just a free-form request:

```bash
uv run bin/run_agent.py "Fix the N+1 query in profiles list endpoint"
```

In this mode, the agent creates **ephemeral state** — a temporary `workflow_state.json`, `scratch.md`, and `scratch_<story_id>.md` in a temp directory. The same step machinery runs: the full workflow (context_gathering → planning → ... → final_review), step editing, guardrails, and history tracking all work identically. The request text is used as the story description. When the agent finishes, the ephemeral state is discarded.

This means:
- **The agent code is the same** in one-shot and PRD modes — the orchestrator constructs the workflow state and passes the path and story ID to `run_agent.py` in both cases
- **Step editing and restart work** — the ephemeral state file backs them
- **One-shot is the natural starting point for implementation** — get a single agent's workflow working end-to-end, then layer on the orchestrator and parallel execution

One-shot mode is useful for quick one-off tasks that don't warrant a full PRD, and for testing the agent workflow during development.

## Known Limitations & Accepted Risks

### Infrastructure resource consumption

Each parallel agent runs a full Docker Compose stack (MySQL, Redis, Kafka, TimescaleDB) for test isolation. At ~1-2GB RAM per agent, this limits practical parallelism to 3-5 agents on most machines. A future optimization is shared infrastructure with per-agent database isolation (separate DB names, Redis database numbers), which would reduce RAM usage by 60-80%.

### Docker socket sharing

Agent containers mount the host Docker socket (`/var/run/docker.sock`) to run test infrastructure. This gives agents full Docker daemon access on the host. This is an accepted risk — agents are trusted code execution environments, not sandboxes. The socket is required for agents to run tests via `docker compose`.

### Container startup overhead

Each step is a separate container invocation (~5-10 seconds overhead per step). For a 10-step story, this adds ~50-100 seconds of pure overhead. Future optimizations include warm containers (persistent container per agent that accepts step payloads) and collapsing read-only steps.

### Self-review limitations

The `review` and `final_review` steps are self-review — the same agent (model) that wrote the code reviews it. This means systematic misunderstandings of requirements may persist through review. The per-step instructions mitigate this by requiring concrete acceptance criteria verification with file/line citations, and `final_review` runs objective automated checks (tests + lint).

## Implementation Order

The system should be built bottom-up:

1. **Single agent, one-shot mode** — implement the step execution loop, step editing, restart, scratch files, per-step timeouts. Test with `run_agent.py "some task"`. This is the foundation — get it working and polished before moving on.

2. **PRD mode, single agent:**
   - **2a. Persistent state + step orchestrator** — add `workflow_state.json` persistence, step-level orchestration for a single story. Test full workflow lifecycle.
   - **2b. Multi-story serial execution** — add story assignment from `prd.json`, dependency resolution, serial orchestrator loop.
   - **2c. Failure propagation and recovery** — add `blocked` status, failure propagation to dependents, orchestrator crash recovery.

3. **Parallel execution** — add `git worktree` isolation, multi-agent orchestrator, `FileLock`-based state coordination, rebase-then-merge strategy, structured logging.

## Concrete JSON Examples

### Full `workflow_state.json`

Two stories: one completed, one in progress with a workflow edit.

```json
{
  "version": 1,
  "created_at": "2025-01-15T10:00:00Z",
  "prd_file": "prd.json",
  "stories": {
    "US-001": {
      "story_id": "US-001",
      "title": "Add status field to profiles model",
      "status": "completed",
      "agent_id": 1,
      "claimed_at": "2025-01-15T10:05:00Z",
      "completed_at": "2025-01-15T10:45:00Z",
      "depends_on": [],
      "steps": [
        {
          "id": "step-001",
          "type": "context_gathering",
          "status": "completed",
          "description": "Explore codebase and gather context for profiles status field",
          "started_at": "2025-01-15T10:05:30Z",
          "completed_at": "2025-01-15T10:08:00Z",
          "git_sha_at_start": "a1b2c3d",
          "notes": "Found: profiles/models.py (Profile model), profiles/schemas.py, test_profiles.py. No existing status field.",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.08,
          "input_tokens": 12000,
          "output_tokens": 2500,
          "log_file": "logs/US-001/step-001.jsonl"
        },
        {
          "id": "step-002",
          "type": "planning",
          "status": "completed",
          "description": "Produce implementation plan for status field",
          "started_at": "2025-01-15T10:08:05Z",
          "completed_at": "2025-01-15T10:11:00Z",
          "git_sha_at_start": "a1b2c3d",
          "notes": "Plan: add ProfileStatus enum and status column to Profile model, create migration, update serializer",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.10,
          "input_tokens": 14000,
          "output_tokens": 3000,
          "log_file": "logs/US-001/step-002.jsonl"
        },
        {
          "id": "step-005",
          "type": "coding",
          "status": "completed",
          "description": "Implement status field changes",
          "started_at": "2025-01-15T10:16:05Z",
          "completed_at": "2025-01-15T10:25:00Z",
          "git_sha_at_start": "a1b2c3d",
          "notes": "Added ProfileStatus enum, status column, migration, updated serializer",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.45,
          "input_tokens": 20000,
          "output_tokens": 8000,
          "log_file": "logs/US-001/step-005.jsonl"
        },
        {
          "id": "step-010",
          "type": "final_review",
          "status": "completed",
          "description": "Final verification and commit",
          "started_at": "2025-01-15T10:38:05Z",
          "completed_at": "2025-01-15T10:42:00Z",
          "git_sha_at_start": "f5e6d7c",
          "notes": "All checks pass. Committed as 'feat: add status field to profiles'.",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.15,
          "input_tokens": 18000,
          "output_tokens": 2000,
          "log_file": "logs/US-001/step-010.jsonl"
        }
      ],
      "history": [
        {
          "timestamp": "2025-01-15T10:05:00Z",
          "action": "story_claimed",
          "agent_id": 1,
          "step_id": null,
          "details": {}
        },
        {
          "timestamp": "2025-01-15T10:42:00Z",
          "action": "story_completed",
          "agent_id": 1,
          "step_id": null,
          "details": {}
        }
      ]
    },
    "US-002": {
      "story_id": "US-002",
      "title": "Add profile status API endpoint",
      "status": "in_progress",
      "agent_id": 2,
      "claimed_at": "2025-01-15T10:45:05Z",
      "completed_at": null,
      "depends_on": ["US-001"],
      "steps": [
        {
          "id": "step-007",
          "type": "initial_testing",
          "status": "completed",
          "description": "Run tests for status endpoint",
          "started_at": "2025-01-15T11:06:05Z",
          "completed_at": "2025-01-15T11:12:00Z",
          "git_sha_at_start": "b2c3d4e",
          "notes": "2 of 4 tests failed: test_invalid_transition and test_unauthorized",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.20,
          "input_tokens": 16000,
          "output_tokens": 3000,
          "log_file": "logs/US-002/step-007.jsonl"
        },
        {
          "id": "step-011",
          "type": "coding",
          "status": "completed",
          "description": "Fix failing tests: invalid transition validation and auth check",
          "started_at": "2025-01-15T11:12:10Z",
          "completed_at": "2025-01-15T11:18:00Z",
          "git_sha_at_start": "c3d4e5f",
          "notes": "Fixed transition validation logic and added proper permission check",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.35,
          "input_tokens": 18000,
          "output_tokens": 6000,
          "log_file": "logs/US-002/step-011.jsonl"
        },
        {
          "id": "step-012",
          "type": "linting",
          "status": "completed",
          "description": "Re-run lint after fixes",
          "started_at": "2025-01-15T11:18:05Z",
          "completed_at": "2025-01-15T11:19:00Z",
          "git_sha_at_start": "d4e5f6a",
          "notes": "Clean",
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": 0.05,
          "input_tokens": 10000,
          "output_tokens": 500,
          "log_file": "logs/US-002/step-012.jsonl"
        },
        {
          "id": "step-013",
          "type": "initial_testing",
          "status": "in_progress",
          "description": "Re-run tests after fixes",
          "started_at": "2025-01-15T11:19:05Z",
          "completed_at": null,
          "git_sha_at_start": "d4e5f6a",
          "notes": null,
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": null,
          "input_tokens": null,
          "output_tokens": null,
          "log_file": "logs/US-002/step-013.jsonl"
        },
        {
          "id": "step-008",
          "type": "review",
          "status": "pending",
          "description": "Self-review against acceptance criteria",
          "started_at": null,
          "completed_at": null,
          "git_sha_at_start": null,
          "notes": null,
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": null,
          "input_tokens": null,
          "output_tokens": null,
          "log_file": null
        },
        {
          "id": "step-009",
          "type": "prune_tests",
          "status": "pending",
          "description": "Remove redundant tests",
          "started_at": null,
          "completed_at": null,
          "git_sha_at_start": null,
          "notes": null,
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": null,
          "input_tokens": null,
          "output_tokens": null,
          "log_file": null
        },
        {
          "id": "step-010",
          "type": "final_review",
          "status": "pending",
          "description": "Final verification and commit",
          "started_at": null,
          "completed_at": null,
          "git_sha_at_start": null,
          "notes": null,
          "error": null,
          "skip_reason": null,
          "restart_count": 0,
          "cost_usd": null,
          "input_tokens": null,
          "output_tokens": null,
          "log_file": null
        }
      ],
      "history": [
        {
          "timestamp": "2025-01-15T10:45:05Z",
          "action": "story_claimed",
          "agent_id": 2,
          "step_id": null,
          "details": {}
        },
        {
          "timestamp": "2025-01-15T11:12:00Z",
          "action": "workflow_edit",
          "agent_id": 2,
          "step_id": "step-007",
          "details": {
            "operation": "add_after",
            "reason": "2 tests failed — need a coding fix, re-lint, and re-test before review",
            "new_steps": [
              {
                "id": "step-011",
                "type": "coding",
                "description": "Fix failing tests: invalid transition validation and auth check"
              },
              {
                "id": "step-012",
                "type": "linting",
                "description": "Re-run lint after fixes"
              },
              {
                "id": "step-013",
                "type": "initial_testing",
                "description": "Re-run tests after fixes"
              }
            ]
          }
        }
      ]
    }
  }
}
```

Note: The US-001 example is abbreviated (showing only key steps) for readability. A real workflow would include all 10 default steps.

### Step Editing Examples

**`add_after`** -- insert steps after a completed step:

```json
{
  "operation": "add_after",
  "target_step_id": "step-007",
  "reason": "2 tests failed — need fix cycle before review",
  "new_steps": [
    { "type": "coding", "description": "Fix transition validation and auth check" },
    { "type": "linting", "description": "Re-lint after fixes" },
    { "type": "initial_testing", "description": "Re-run tests after fixes" }
  ]
}
```

New steps receive monotonically increasing IDs: `step-011`, `step-012`, `step-013`.

**`split`** -- replace a pending step with multiple steps:

```json
{
  "operation": "split",
  "target_step_id": "step-005",
  "reason": "Story requires both model changes and migration — split into two focused steps",
  "replacement_steps": [
    { "type": "coding", "description": "Add model changes and migration" },
    { "type": "coding", "description": "Add API endpoint and serializer" }
  ]
}
```

**`skip`** -- mark a pending step as skipped:

```json
{
  "operation": "skip",
  "target_step_id": "step-009",
  "reason": "Only 2 tests added, no redundancy to prune"
}
```

**`reorder`** -- change the order of pending steps:

```json
{
  "operation": "reorder",
  "reason": "Need to write tests before production code (TDD approach)",
  "new_order": ["step-005", "step-004", "step-006", "step-007", "step-008", "step-009", "step-010"]
}
```

The `new_order` array must contain exactly the set of all pending step IDs. `final_review` must remain last.

**`restart`** -- edit description and re-execute the current step:

```json
{
  "operation": "restart",
  "target_step_id": "step-005",
  "reason": "Went down the wrong path — tried to modify the serializer instead of the model",
  "new_description": "Implement status field by adding column to Profile model and generating migration"
}
```

The step's `restart_count` is incremented. Max 3 restarts per step.

**`edit_description`** -- modify a pending step's description:

```json
{
  "operation": "edit_description",
  "target_step_id": "step-007",
  "reason": "Need to also test the migration rollback",
  "new_description": "Run tests for status field including migration rollback test"
}
```

To edit the description of the currently executing (`in_progress`) step, use `restart` instead.

## Default Workflow Template

When a story is claimed, it gets this default set of steps:

```json
[
  { "id": "step-001", "type": "context_gathering",  "status": "pending", "description": "Explore codebase, DB schema, docs, and related code" },
  { "id": "step-002", "type": "planning",           "status": "pending", "description": "Produce implementation plan based on gathered context" },
  { "id": "step-003", "type": "architecture",       "status": "pending", "description": "Design code structure and identify files to modify" },
  { "id": "step-004", "type": "test_architecture",  "status": "pending", "description": "Design test strategy and identify test files" },
  { "id": "step-005", "type": "coding",             "status": "pending", "description": "Implement the changes" },
  { "id": "step-006", "type": "linting",            "status": "pending", "description": "Run formatters and lint checks" },
  { "id": "step-007", "type": "initial_testing",    "status": "pending", "description": "Run tests and identify failures" },
  { "id": "step-008", "type": "review",             "status": "pending", "description": "Self-review against acceptance criteria" },
  { "id": "step-009", "type": "prune_tests",        "status": "pending", "description": "Remove redundant tests" },
  { "id": "step-010", "type": "final_review",       "status": "pending", "description": "Final verification and commit" }
]
```

All steps start with `status: "pending"`, null timestamps, null notes, `restart_count: 0`, and null cost/token fields. The orchestrator populates these default values when creating the workflow. The next dynamically added step will use `step-011`.
