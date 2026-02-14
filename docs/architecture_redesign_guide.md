# Architecture Redesign Guide

A guide for making good architectural decisions and planning migrations.
Applicable to any codebase — the evaluation criteria, anti-patterns,
framing methodology, and comparison process are universal. The Design
Questions section is project-specific and should be replaced when
applying to a different project.

Optionally uses parallel agents with convergence voting (Delphi method)
— see the appendix for that process. For best results with parallel
agents, extract a repository identity first (see
`docs/identity_extraction.md`).

---

## Table of Contents

- [Context](#context)
- [Project Constraints](#project-constraints)
- [Evaluation Criteria](#evaluation-criteria)
- [Design Anti-patterns](#design-anti-patterns)
- [Framing the Question](#framing-the-question)
- [What Makes a Good Proposal](#what-makes-a-good-proposal)
- [Comparing and Choosing](#comparing-and-choosing)
- [Migration Planning](#migration-planning)
- [Design Questions to Resolve](#design-questions-to-resolve)
- [Templates Reference](#templates-reference)
- [Appendix: Running with Parallel Agents](#appendix-running-with-parallel-agents)

**Companion documents:**
- `docs/identity_extraction.md` — How to extract differentiated agent
  identities from any repository (recommended for parallel agent work).

---

## Context

> **Note:** This section is project-specific. When applying this guide
> to a different project, replace it with your project's context and
> pain points. Everything from Evaluation Criteria onward is reusable.

Dynamic Ralph is a `uvx` CLI tool that runs inside Docker. It mounts
the user's `cwd` into `/workspace` and orchestrates coding agents
through step-based workflows to implement PRD stories. The codebase
lives at `/dynamic_ralph` inside the container.

**Current pain points:** closed step types, rigid PRD models, no user
configuration surface, no prompt customization, fragile parallel
orchestration. See `docs/architecture_improvements.md` for the full
inventory.

---

## Project Constraints

> **Note:** These constraints are project-specific. Replace them with
> your project's constraints when applying this guide elsewhere.

All proposals MUST respect these non-negotiable constraints:

1. **Deployment model:** `uvx` command running inside Docker.
   Code at `/dynamic_ralph`, user repo mounted at `/workspace`.
   NOT a pip-installable library. NOT importable by users.

2. **Extensibility surface:** `.ralph.toml` in user's project root
   (mounted at `/workspace/.ralph.toml`). Environment variables for
   overrides. Mounted prompt directories for custom instructions.
   NO Python plugin imports from user code.

3. **Backward compatibility:** Existing PRD formats (both flat array
   and rich object) must continue to work. Existing workflow state
   files must either load or auto-migrate.

4. **Stack:** Python 3.13+, Pydantic v2, `uv` for package management,
   `ruff` for linting. No new heavy dependencies.

5. **Test coverage:** Every behavioral change needs tests. Use
   `uv run pytest`. Use `uv run pre-commit run -a` for lint.

6. **Commit convention:** `<component>: <lowercase verb phrase>`.
   Components: orchestrator, executor, prompts, workflow, backend,
   models, tests, infra, docs, gitignore, runner, scratch,
   retrospective.

---

## Evaluation Criteria

Every proposal is evaluated on these five criteria. These define what
"good" means for this project — read them before writing or reviewing
any proposal.

| Criterion | Weight | What it measures | Anchors |
|-----------|--------|-----------------|---------|
| **Simplicity** | 25% | Fewer files, fewer abstractions, less indirection. Could a new contributor understand it in 10 minutes? Does it keep prompt composition compact (fewer injected tokens per step)? A proposal with internally inconsistent patterns (mixing dataclasses, Pydantic, and TypedDicts for the same concept) is penalized here. | 5 = no new files/abstractions; 1 = 3+ new abstractions |
| **Extensibility** | 25% | Can users add step types, story kinds, prompts, and backends through configuration (`.ralph.toml`, env vars, mounted directories) without forking? | 5 = config-only extension; 1 = requires code changes in 3+ files |
| **Migration Safety** | 20% | Does it break existing PRDs, state files, or workflows? Is there a concrete auto-migration path? | 5 = fully backward compatible; 1 = breaking change with no auto-migration |
| **Debuggability** | 15% | When a step fails at 2 AM, can the operator reconstruct what happened? Does the proposal preserve or improve structured history, log correlation, diff capture, and state inspectability? | 5 = adds structured logging, preserves full history chain, errors include file:line context; 3 = no change to current observability; 1 = removes state files, loses history entries, or makes errors less traceable |
| **Testability** | 15% | Can the change be tested in isolation without Docker, subprocesses, or file system side effects? Does it make existing tests simpler or harder? | 5 = pure functions, easy mocking; 1 = requires integration test with Docker |

**Computing weighted totals:**
`(S*0.25 + E*0.25 + M*0.20 + D*0.15 + T*0.15)` gives a score out of
5.0. The approach with the highest weighted average wins, subject to
the veto rule and verification against success criteria. Weighted
averages are a decision heuristic, not a proof of correctness — always
sanity-check the winner against the success criteria.

### Evaluation Scenarios

For each criterion, write 1-2 concrete scenarios that test how a
proposal handles a specific user action or system behavior. This
replaces vibe-based "Extensibility: 4/5" with grounded, comparable
assessments. (Inspired by ATAM — Architecture Tradeoff Analysis Method.)

Examples:
- **Extensibility:** "A user wants to add a `security-scan` step that
  runs bandit after coding. How many files do they touch?"
- **Migration Safety:** "An existing `workflow_state.json` references
  `StepType.coding`. Does it load or fail?"
- **Debuggability:** "A coding step times out at 2 AM. Can the operator
  reconstruct what happened from logs alone?"

Include these scenarios in comparison documents — evaluators assess
each proposal against them.

### Why Not "Consistency"? *(Dynamic Ralph specific)*

When the existing codebase has known structural problems (4 parallel
dicts, dual PRD models, closed enum), scoring "consistency with
current patterns" creates conservatism bias — it penalizes the
proposals that fix the problems. Internal coherence of a proposal is
captured under Simplicity instead.

---

## Design Anti-patterns

These encode design wisdom about what makes proposals fail. Use C
prefixes when referencing them (e.g., "this proposal risks C1").
Check every proposal against these before submitting or voting.

**C1: Over-abstraction — layering multiple extension mechanisms.**
Don't add a registry, a factory, a protocol, AND a plugin system for
the same concept. Pick one mechanism.
> *Instead:* Choose the mechanism that requires the fewest new files.
> If you're creating more than one new file to support extensibility
> for a single concept, you are over-engineering.

**C2: Premature generalization — designing for the unknown third case.**
Don't design for "any future backend" when there is one backend
(Claude Code). If you cannot name a concrete second user or use case,
you are generalizing prematurely.
> *Instead:* Name the specific second case (e.g., Aider). Show your
> design handles it. If you cannot name one, simplify until you can.

**C3: Config explosion — making everything configurable.**
Don't expose a config knob for every internal constant.
> *Instead:* For every config option you propose, cite the specific
> user need or pain point it addresses. No citation → remove it.

**C4: Breaking changes for aesthetics.**
Don't rename types for cleanliness alone. Include an auto-migration
path in your Migration Plan.
> *Instead:* If auto-migration is impossible, keep the old name and
> add the new one as an alias.

**C5: Ignoring the deployment model.**
Proposals requiring users to install plugins or subclass Python
objects are invalid (see Project Constraints #1 and #2).
> *Instead:* All user-facing extensibility must work through files
> in the user's project directory or environment variables.

**C6: Solving hypothetical problems.**
Every proposal must cite a concrete current limitation, not "what if
someone wants to..."
> *Instead:* Start your Summary with "This solves [specific problem]"
> and reference the file/line where the problem manifests.

**C7: Local optimization — perfecting one component at the expense of others.**
A proposal for Q1 that creates a beautiful step registry but makes
Q3 (configuration) impossible to implement cleanly is a net negative.
> *Instead:* Use the "Downstream Impact" section in your proposal to
> state how your proposal affects later questions. If you cannot
> assess the impact, flag it as a risk.

---

## Framing the Question

Before writing any proposal, frame the question precisely. Poor
framing produces proposals that solve different problems.

1. **Write a one-sentence question.** If you cannot state it in one
   sentence, it is too broad — split it first.
2. **Define the scope boundary.** List what is IN scope and what is
   explicitly OUT of scope.
3. **State the success criteria.** How will you know the winning
   proposal actually solved the problem?
4. **Identify the key files.** List the 3-7 source files that must be
   read to understand the current state.

**Neutral framing rule:** The question must NOT contain a preferred
answer. Write "Should serial mode use worktrees?" not "Serial mode
is fragile — should it use worktrees?" Let the trade-offs be
discovered, not presupposed.

### Framing Template

```
QUESTION: <one sentence, no preferred answer embedded>

SCOPE:
  In scope:
    - <item>
    - <item>
  Out of scope:
    - <item>
    - <item>

SUCCESS CRITERIA:
  1. <measurable criterion>
  2. <criterion>

KEY FILES (3-7):
  - <path/to/file.py> — <why this file matters>

IDENTITY: <path to identity document, or "none">
TIER: <0 | 1 | 2> (see docs/identity_extraction.md, "Three Tiers")
RELEVANT FACETS: <comma-separated list, e.g. "Architecture Map, Known Debt">
```

**Identity documents** provide shared factual grounding for parallel
agents, reducing correlated outputs. See `docs/identity_extraction.md`
for the full extraction methodology. Summary:
- **Tier 0** (no identity): Narrow questions, <5 key files.
- **Tier 1** (shared identity): Standard for most questions, >3K LOC.
- **Tier 2** (identity + per-role context slices): Broad questions or
  prior rounds with high convergence despite role differentiation.

### Framing Example (Q1)

```
QUESTION: How should step types be made extensible so that adding a new
step type requires touching at most 1 file?

SCOPE:
  In scope:
    - StepType enum and its 4 parallel dicts
    - Step model validation
    - executor timeout/permission lookups
  Out of scope:
    - Configuration file format (Q3)
    - PRD model changes (Q2)
    - Prompt content (only prompt lookup mechanism)

SUCCESS CRITERIA:
  1. Adding a new step type requires touching at most 1 file
  2. Zero changes to existing code (open-closed principle)
  3. Existing workflow_state.json files load without migration

KEY FILES:
  - multi_agent/workflow/models.py — StepType enum, Step model
  - multi_agent/workflow/steps.py — 4 parallel dicts, default workflow
  - multi_agent/workflow/prompts.py — STEP_INSTRUCTIONS dict
  - multi_agent/workflow/executor.py — timeout lookup
  - multi_agent/workflow/editing.py — validation against StepType

IDENTITY: docs/decisions/working/identity.md
RELEVANT FACETS: Architecture Map, Known Debt
```

---

## What Makes a Good Proposal

### Concrete Code, Not Prose

A proposal that says "we should add a registry" is not a proposal — it
is a suggestion. Good proposals contain:

- Complete type definitions (dataclass/model definitions, enums, config
  schemas)
- Function signatures with docstrings
- Elided function bodies (`...`) unless the body IS the design decision
  (e.g., a migration function)
- Specific file paths and what changes in each
- NOT pseudocode; NOT 200 lines of implementation

### Quality Checks

Before submitting, verify your proposal passes these checks:

| # | Check |
|---|-------|
| QG-1 | Contains actual Python code (type definitions, function signatures) — not just prose |
| QG-2 | References specific files from the codebase by path |
| QG-3 | Includes a Migration Plan section with "What breaks" filled in |
| QG-4 | Fills in all 5 rows of the Self-Assessment table with scores and rationale |
| QG-5 | Role compliance: the proposal reflects its assigned perspective. A Contrarian without a "why the current design suffices" argument, or a Minimalist introducing 3+ new abstractions, fails. |

### Migration and Compatibility

Every proposal must address:
- **What breaks:** Specific list of incompatibilities.
- **Auto-migration path:** Concrete steps, or "none needed."
- **Backward compatibility:** How old formats continue to work.

### Thinking Through Multiple Lenses

To avoid blind spots, evaluate your proposal through these four
perspectives before submitting:

| Lens | Question to ask yourself |
|------|------------------------|
| **Minimalist** | Does this have the fewest new abstractions? Could the problem be solved by modifying existing code without new classes, protocols, or registries? |
| **Extensibility-first** | Can end users customize this via `.ralph.toml` or mounted files, with zero code changes for the most common case? |
| **Migration-conservative** | Does this break any existing PRD format, state file, or workflow? If a breaking change is unavoidable, is there an automatic migration that runs on first load? |
| **Contrarian** | Why might the current design actually be sufficient? If I still propose a change, does it address a limitation that cannot be solved by adjusting usage patterns? |

A strong proposal survives scrutiny from all four lenses. If it
collapses under any one of them, it needs revision. The Anti-pattern
Check and Downstream Impact sections in the Proposal Format template
capture the output of this exercise — fill those in as you work
through the lenses.

### Self-Assessment Against Criteria

Score your own proposal against each of the five evaluation criteria
(1-5 scale with rationale). Free-form "pros/cons" invites minimizing
your own weaknesses. Self-scoring against the actual rubric forces
honest engagement.

---

## Comparing and Choosing

When evaluating multiple proposals (whether your own alternatives or
proposals from different contributors):

### Group into Distinct Approaches

Often multiple proposals produce 3-4 unique strategies with minor
variations. Group them. Name each approach clearly (e.g., "Registry
Pattern", "Config-Driven", "Descriptor Dataclass").

### Build a Comparison Matrix

Rows = approaches, columns = evaluation criteria. Fill in a
qualitative summary for each cell, then apply the evaluation scenarios
to ground the comparison.

### Check for Convergence vs. Divergence

If most proposals independently chose the same approach, that is
evidence (not proof) of a good answer. If proposals split evenly,
check whether they are solving different interpretations of the
question — if so, the question needs reframing.

### Integration with Prior Decisions

For decisions after the first: verify that each proposal's output
types and interfaces are compatible with prior decisions and constraint
addenda. Proposals incompatible with prior decisions are disqualified
unless they include a compelling argument to revisit.

### Extract Good Ideas from Losing Proposals

Even losing proposals may contain individual ideas worth merging into
the winner. Catalog these separately.

### Construct Hybrids (When Warranted)

If two strong approaches each excel on different criteria, combine
their strongest elements. A hybrid MUST cite which original proposals
it borrows from and why. A hybrid MUST NOT be a kitchen-sink
combination — it should resolve a specific tension between two strong
approaches. Skip if proposals are too similar or too incompatible.

### Decision Rule

A proposal is adopted only when BOTH factors are satisfied:

1. **Sufficient support:** It is the clearly preferred approach.
2. **Meets success criteria:** It demonstrably satisfies all success
   criteria from the framing. If the top proposal fails a criterion,
   check the next-best. If none meet all criteria, the question needs
   refinement.

Success criteria first, convergence second.

### When No Proposal Clearly Wins

If a proposal scores fatally on any single criterion, it cannot be
adopted regardless of its total score — a 5/5 on everything else
does not compensate for a 1 on Migration Safety. Prefer the next-best
proposal, or patch the flaw and re-evaluate. When two proposals are
genuinely tied, prefer the one scoring higher on Migration Safety
(hardest to fix retroactively). For detailed voting-specific tie-
breaking and veto rules, see the
[appendix](#voting-rules-and-convergence-thresholds).

---

## Migration Planning

Migration is where architectural decisions succeed or fail. A
beautiful design that cannot be safely adopted is worthless.

### Constraint Propagation Between Decisions

After each decision is adopted, produce a **constraint addendum**
appended to the project constraints for all subsequent work. The
addendum contains:

1. **Decision summary** (1 paragraph).
2. **Interface contracts** — concrete Python signatures, model shapes,
   or config schema fragments that later proposals MUST conform to.
3. **Ruled-out design space** — approaches that are now incompatible.

See the [Constraint Addendum Template](#constraint-addendum-template)
in Templates Reference.

### Virtual Diffs for Un-implemented Decisions

When decisions are adopted but not yet implemented, later work must
design against the future state, not the current source. Provide
"Virtual Diffs" showing the adopted code sketches labeled: "This code
does not exist yet but will replace the current implementation."

Proposals that reference code structures superseded by prior decisions
must be flagged.

### Decision Reversal Protocol

When implementation reveals the design is wrong:

- **Minor adjustment:** A detail the design missed (e.g., a method
  needs an extra parameter). Update the decision record with an
  addendum. No new evaluation needed.
- **Significant deviation:** A core element doesn't work (e.g.,
  circular imports). Mark the implementation story as failed. Run a
  focused evaluation on the specific sub-problem. The new decision
  record supersedes the relevant section of the original (set
  `Status: superseded` with a pointer).
- **Complete reversal:** The entire approach is wrong. Mark the story
  as failed. Return to proposal stage for the original question, with
  the failed implementation's diff and error logs as additional
  context. Mark the original decision record superseded.

**Principle:** Never silently deviate from a voted-on design. Either
the implementation matches the code contract, or there is an explicit
decision record documenting why it changed.

### Diagnosing Bad Decisions

When a decision fails during implementation, diagnose *why* before
starting over — otherwise you repeat the same mistake:

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Implementation hits issues no proposal anticipated | **Bad framing** — key files or constraints were missing from Phase 0 | Re-frame with the discovered constraints added to scope |
| Winning proposal's self-assessment was wildly inaccurate | **Insufficient evaluation** — evaluation scenarios were too abstract to catch the issue | Write concrete scenarios from the failure and re-evaluate |
| Two criteria conflict in practice (e.g., extensibility vs. simplicity) | **Wrong weights** — the criteria weights didn't reflect actual project priorities | Adjust weights in the framing and re-evaluate |
| The approach works but downstream decisions are impossible | **C7 (local optimization)** — downstream impact wasn't assessed | Run the downstream question first, then revisit |
| Everything scored well but users don't like the result | **Missing criterion** — the evaluation criteria didn't capture what actually matters | Add the missing criterion, re-evaluate |

### Verification Against Decision Records

After implementation, a fresh review checks the code against the
decision record's success criteria. Any deviation must be justified.

### Deriving Implementation Stories

Map each independent set of file changes to a story. Migration plans
become separate stories with `depends_on`. Add a final verification
story that depends on all implementation stories: "Verify DR-NNN
implementation matches code contract."

### Spike Validation

Before finalizing a questionable decision, implement ONLY the
type/model changes in a throwaway branch. Run `uv run pytest`. If
>50% of tests fail due to incompatible type signatures, missing
classes, or circular imports (as opposed to tests that just need
assertion value updates), the design has a fundamental compatibility
problem — go back to the proposal stage with the failures as
additional constraints. Cost: 1 agent invocation. Saves discovering
design flaws after full implementation.

---

## Design Questions to Resolve

> **Note:** This section is specific to the Dynamic Ralph redesign.
> The rest of this guide (evaluation criteria, anti-patterns, framing,
> proposal guidance, comparison methodology, migration planning) is
> reusable for any project. Replace these questions with your own when
> applying the guide elsewhere.

Each of these should go through a full evaluation cycle. They are
ordered by dependency — earlier decisions constrain later ones.
Questions within the same round are independent and MAY be run in
parallel.

### Round 1: Foundation (run Q1 and Q2 in parallel)

Q1 and Q2 are independent — neither constrains the other.

**Q1: How should step types be made extensible?**

Current state: `StepType` is a closed `StrEnum` with 10 values.
4 parallel dicts must stay in sync. Adding a step type touches 5 files.

Key files to read:
- `multi_agent/workflow/models.py` (StepType enum, Step model)
- `multi_agent/workflow/steps.py` (4 parallel dicts, default workflow)
- `multi_agent/workflow/prompts.py` (STEP_INSTRUCTIONS dict)
- `multi_agent/workflow/executor.py` (timeout lookup)
- `multi_agent/workflow/editing.py` (validation against StepType)

Read the files listed above. Propose a concrete solution with code.
Do NOT just list options — pick one approach and show it working.

Success criteria: adding a new step type requires touching at most 1
file and zero changes to existing code.

**Q2: How should PRD models be unified?**

Current state: `UserStory` (strict, `US-\d{3}` format) vs `FlatStory`
(lenient, all optional). `parse_prd` returns `Prd | list[FlatStory]`.

Key files to read:
- `multi_agent/models.py` (UserStory, FlatStory, Prd, parse_prd)
- `multi_agent/prd.py` (load_prd, load_prd_model — some dead code)
- `multi_agent/workflow/state.py` (initialize_state_from_prd)
- `multi_agent/__init__.py` (public re-exports of FlatStory, Prd, etc.)

Read the files listed above. Propose a concrete solution with code.
Do NOT just list options — pick one approach and show it working.

Success criteria: `parse_prd` returns a single type. Existing PRD
files parse without changes.

### Round 2: Configuration (depends on Q1)

Q3 depends on Q1 (step extensibility determines what step properties
are configurable). It does NOT strictly depend on Q2.

**Q3: What should the configuration system look like?**

Current state: 9 env vars in `constants.py`. No config file. No way
for users to customize step sequences, timeouts, or prompts.

This question bundles two sub-problems that may need splitting: (a)
configuration file format and loading (mechanical), and (b)
configuration surface area — what should be user-configurable
(product/design decision).

Key files to read:
- `multi_agent/constants.py`
- `multi_agent/workflow/steps.py` (what's configurable)
- `multi_agent/workflow/prompts.py` (what users might override)
- `multi_agent/backends/claude_code.py` (hardcoded Docker config)
- `bin/run_dynamic_ralph.py` (CLI parser — current config surface)

Read the files listed above. Propose a concrete solution with code.
Do NOT just list options — pick one approach and show it working.
Address both the file format and what should be configurable vs.
hardcoded.

Success criteria: users can customize step timeouts and prompts
via a config file without touching any Python code.

### Round 3: Pipeline and Communication (run Q4 and Q5 in parallel)

Depends on: Q3 (configuration system determines how modes and
communication are configured). Q4 and Q5 are independent of each
other.

**Q4: How should the executor pipeline be decomposed?**

Current state: `execute_step` in `executor.py` is ~370 lines handling
seven concerns in one function: git state capture, step status
management, prompt composition, agent launching, summary extraction,
workflow edit processing, and failure/timeout/success branching.
`bin/run_dynamic_ralph.py` is ~980 lines with three execution modes
(`run_serial`, `run_parallel`, `run_one_shot`) as distinct code paths.
The agent launch pattern is duplicated in `bin/run_retrospective.py`.
Parallel mode uses worktrees; serial/one-shot modes do not.

Key files to read:
- `multi_agent/workflow/executor.py` (monolithic execute_step)
- `bin/run_dynamic_ralph.py` (three modes, worktree helpers)
- `bin/run_retrospective.py` (duplicated agent launch logic)
- `multi_agent/workflow/state.py` (reset_in_progress, find_assignable_story)

Read the files listed above. Propose a concrete solution with code.
Do NOT just list options — pick one approach and show it working.

Success criteria: (1) adding a new execution mode does not require
duplicating agent launch logic, (2) a step timeout or crash in any
mode does not leave the working tree dirty.

**Q5: How should multi-agent communication work?**

Current state: global scratch (free-form markdown) and per-story
scratch. No structure, no topics, no superseding. The entire scratch
file contents are dumped into the prompt (`compose_step_prompt`
lines 261-267), which does not scale.

This question has three sub-dimensions: (a) write format — how
entries are stored, (b) read strategy — how entries are
selected/filtered for inclusion in prompts, (c) concurrency model —
global scratch is written by all agents concurrently.

Key files to read:
- `multi_agent/workflow/scratch.py`
- `multi_agent/workflow/executor.py` (how scratch is written)
- `multi_agent/workflow/prompts.py` (how scratch is read into prompts)
- `multi_agent/filelock.py` (concurrent access to global scratch)
- `multi_agent/workflow/models.py` (HistoryEntry — existing structured metadata)

Read the files listed above. Propose a concrete solution with code.
Do NOT just list options — pick one approach and show it working.
Address all three sub-dimensions (write, read, concurrency).

Success criteria: (1) agents can write structured entries with a
topic key and retrieve entries by topic in O(1) lookups (not linear
scan of all history), (2) `compose_step_prompt` injects only the
latest entry per topic (not the full scratch history), (3) concurrent
writes from parallel agents do not corrupt the scratch file.

### Post-implementation Cleanup (no vote needed)

These items are straightforward and do not require a full evaluation
cycle. Execute them after the design questions are resolved.

| Item | Description | Key files |
|------|-------------|-----------|
| **C1: Dead code removal** | Remove unused `compose()` and `compose_bare()` in `compose.py`, dead `load_prd`/`find_next_story` in `prd.py`, dead `base_instructions` parameter in `compose_step_prompt` | `multi_agent/compose.py`, `multi_agent/prd.py`, `multi_agent/workflow/prompts.py` |
| **C2: Package restructuring** | Move orchestrator logic from `bin/` into `multi_agent/`. Clean up `__init__.py` exports. Should follow from Q4 decisions. | `bin/run_dynamic_ralph.py`, `multi_agent/__init__.py` |
| **C3: Typed AgentEvent.kind** | Replace `str` with `StrEnum`. Small, non-controversial. | `multi_agent/stream.py`, `multi_agent/backend.py` |

---

## Templates Reference

### Proposal Format (Phase 1)

Every proposal MUST follow this structure:

```markdown
## Approach: <short-name-slug>

### Summary
<2-3 sentences. What is the core idea? What pattern does it follow?>

### Scope
- **Files touched:** <count>
- **Estimated LOC changed:** <number or range>
- **New abstractions introduced:** <list or "none">
- **Effort:** S / M / L / XL

### Code Sketch
<Complete type definitions (models, enums, config schemas).
Function signatures with docstrings. Elide function bodies with `...`
unless the body IS the design decision (e.g., a migration function).
NOT pseudocode. NOT 200 lines of implementation.>

### Files Changed
- `path/to/file.py` — what changes and why

### Migration Plan
- **What breaks:** <specific list>
- **Auto-migration path:** <concrete steps or "none needed">
- **Backward compatibility:** <how old formats continue to work>

### Test Plan
- <What new test cases are needed?>
- <What existing tests break and how to fix them?>
- <Can it be tested without Docker or subprocesses?>

### Self-Assessment

| Criterion        | Self-Score (1-5) | Rationale |
|------------------|-----------------|-----------|
| Simplicity       |                 |           |
| Extensibility    |                 |           |
| Migration Safety |                 |           |
| Debuggability    |                 |           |
| Testability      |                 |           |

### Downstream Impact
- **Constraints on future questions:** <what later rounds must
  respect if this approach wins>
- **Impact on later questions:** <how this proposal affects Q3, Q4,
  Q5, etc., or "none — this is self-contained">

### Anti-pattern Check
- [ ] Not over-abstracting (C1): <1 sentence>
- [ ] Not prematurely generalizing (C2): <name the concrete second
  use case, or "N/A — single mechanism">
- [ ] Not creating config explosion (C3): <for each config option,
  cite the user need>
- [ ] Not breaking for aesthetics (C4): <migration path exists or
  rename is aliased>
- [ ] Respects deployment model (C5): <no Python imports from user
  code>
- [ ] Solves a concrete problem (C6): <cite file:line where problem
  manifests>
- [ ] Not locally optimizing (C7): <downstream impact on later
  questions assessed>
```

**Why Self-Assessment instead of "Trade-offs" / "Alternatives
Considered"?** Free-form pros/cons invite minimizing weaknesses.
Self-scoring against the actual rubric forces honest engagement with
the criteria. "Alternatives Considered" belongs in the comparison
phase (where all proposals are visible), not in individual proposals.

### Synthesis Format (Phase 2)

```markdown
## Synthesis: Q<number>

### Approaches Identified
1. **<Name A>** — <1-sentence summary> (proposed by agents: 1, 4, 7)
2. **<Name B>** — <1-sentence summary> (proposed by agents: 2, 3, 5, 8)
3. **<Name C>** — <1-sentence summary> (proposed by agents: 6, 9, 10)

### Convergence: <ratio, e.g. "4/10 on B">

### Comparison Matrix

| Criterion | A | B | C |
|-----------|---|---|---|
| Simplicity | <qualitative> | <qualitative> | <qualitative> |
| Extensibility | ... | ... | ... |
| Migration Safety | ... | ... | ... |
| Debuggability | ... | ... | ... |
| Testability | ... | ... | ... |

### Evaluation Scenarios
For each scoring criterion, 1-2 concrete scenarios:
- Extensibility: "A user adds a `security-scan` step. With A: <how>.
  With B: <how>. With C: <how>."
- Migration Safety: "An existing workflow_state.json references
  `StepType.coding`. With A: <loads/fails>. With B: <loads/fails>."
- <... one scenario per criterion>

### Novel Ideas Worth Preserving
- From agent 6: <idea> (could merge into any approach)
- From agent 9: <idea>

### Hybrid Proposals (if constructed)
For each hybrid:
- **Hybrid H1: <name>** — combines <element> from Approach <X> with
  <element> from Approach <Y>.
  - Rationale: <why this combination resolves a specific tension>
  - Full proposal: (attached separately, same format as Phase 1)

### Alternatives Considered (cross-proposal)
<Ideas that appeared in losing proposals but are worth noting.
This is where "why not X?" gets answered for the record.>

### Divergence Diagnosis (if split)
<Are agents solving different problems, or disagreeing on the solution?>

### Identity Violations (if identity document provided)
<Proposals that contradict invariants or constraints documented in
the identity. Cite the specific identity section and proposal claim.>

### Open Questions for Voters
<Anything the synthesizer could not resolve from the proposals alone.>
```

### Vote Format (Phase 3)

```markdown
## Vote: Q<number>

### Scores (1-5 per criterion, weighted)

| Approach | Simplicity (x0.25) | Extensibility (x0.25) | Migration Safety (x0.20) | Debuggability (x0.15) | Testability (x0.15) | Weighted Total |
|----------|--------------------|-----------------------|--------------------------|----------------------|---------------------|----------------|
| A        | 4 (1.00)           | 5 (1.25)              | 3 (0.60)                 | 4 (0.60)             | 4 (0.60)            | 4.05           |
| B        | 3 (0.75)           | 4 (1.00)              | 5 (1.00)                 | 3 (0.45)             | 3 (0.45)            | 3.65           |

### Per-Criterion Justification
For EACH approach on EACH criterion, provide a one-sentence reason:
- A / Simplicity (4): <why>
- A / Extensibility (5): <why>
- B / Simplicity (3): <why>
- B / Extensibility (4): <why>
- ... (all cells)

### Winner: <approach name>

### Merge Suggestions (optional)
<If you see value in combining elements from multiple approaches:>
- Take <element> from Approach <X> because ...
- Combine with <element> from Approach <Y> because ...

### Dissent (if voting against the majority)
<If you are voting against what appears to be the consensus, explain
what flaw you see that others may be overlooking.>

### Anti-pattern Flags
<List any C anti-patterns observed in the proposals you scored.
Example: "Approach B risks C1 — it introduces a registry, a factory,
AND a protocol for step types.">
```

**Why per-criterion justification?** Free-form "Justification" bullets
lead evaluators to justify only their winner and handwave the rest.
Requiring a reason for every score ensures all approaches get fair
evaluation.

### Decision Record Format (Phase 4)

```markdown
## Decision: Q<number> — <short title>

### Status: accepted | accepted-with-review | escalated | superseded
### Superseded-by: DR-<NNN> (if applicable)
### Date: <YYYY-MM-DD>
### Question: <the original framed question>

### Decision
<2-3 sentences describing the chosen approach>

### Rationale
- Convergence: <X/N proposals chose this, Y/N voted for it>
  (If the winner is a hybrid, state which original proposals
  it combines and note "0/N in proposals" — hybrids have no
  proposal-phase convergence by definition.)
- Key strengths: ...
- Key risks and mitigations: ...

### Success Criteria
<copied from framing, used for verification>

### Code Contract
<Key type definitions, function signatures, and config schemas from
the winning proposal's code sketch. This is the binding specification
for implementation — deviations require an explicit addendum.>

### Files Changed
- `path/to/file.py` — what changes and why
<Derived from the winning proposal's Files Changed section.>

### Validation Plan (if weak win)
- After implementation, check: ...
- If validation fails, reconsider: ...

### Implementation Notes
- Start with: <file>
- Key constraint: ...
```

### Constraint Addendum Template

Produced after each decision to propagate constraints forward:

```
## Constraint Addendum: Q{N} — {short title}
Date: {YYYY-MM-DD}
Decision Record: DR-{NNN}-{slug}.md

### Decision Summary
{1 paragraph: what was decided and why}

### Interface Contracts
Subsequent proposals MUST conform to these signatures/schemas:

  # From {file_path}
  {concrete Python signatures, dataclass definitions, or config
   schema fragments}

### Ruled-Out Design Space
The following approaches are now incompatible with this decision:
- {approach}: {why it is incompatible}

### Virtual Diff (for un-implemented decisions)
The following code does NOT exist yet but will replace the current
implementation. Design against this, not the current source.

  # {file_path} — WILL REPLACE current lines {N}-{M}
  {adopted code sketch from the decision record}
```

**Example (Q1 decision propagating to Q3):**

```
## Constraint Addendum: Q1 — Step Type Registry
Date: 2025-01-15
Decision Record: DR-001-step-type-registry.md

### Decision Summary
Step types are defined as StepDescriptor dataclasses registered in a
central registry. The StepType StrEnum is replaced by string keys.
Lookup uses `registry.get(step_name)` which returns a StepDescriptor
containing timeout, allows_editing, mandatory flag, and instructions.

### Interface Contracts
Subsequent proposals MUST conform to these signatures:

  # From multi_agent/workflow/steps.py
  @dataclass(frozen=True)
  class StepDescriptor:
      name: str
      timeout: int
      allows_editing: bool
      mandatory: bool
      instructions: str

  STEP_REGISTRY: dict[str, StepDescriptor] = {}

  def register_step(descriptor: StepDescriptor) -> None: ...
  def get_step(name: str) -> StepDescriptor: ...

### Ruled-Out Design Space
- Parallel dict approach: incompatible — all step properties are now
  colocated in StepDescriptor.
- Protocol-based approach: ruled out — StepDescriptor is a concrete
  dataclass, not a protocol.

### Virtual Diff
  # multi_agent/workflow/steps.py — WILL REPLACE current lines 12-90
  # (see DR-001 Code Contract for full sketch)
```

---

## Appendix: Running with Parallel Agents

This appendix describes how to use parallel agents (Delphi method) to
produce and evaluate proposals at scale. The main body of this guide
describes *what* makes a good decision; this appendix describes a
*process* for arriving at one.

**A caveat on parallel agents:** The real value is NOT "wisdom of
crowds" (which requires genuinely independent signals). LLM agents
share the same training data, the same prompt, and the same codebase —
their outputs are correlated, not independent. What you actually get:

1. **Forced structured output.** The proposal format makes agents
   produce code sketches, migration plans, and self-assessments that
   a single agent might skip.
2. **Coverage of the design space.** Role-differentiated agents
   (minimalist, extensibility-first, contrarian) explore different
   trade-off axes that a single agent would collapse into one.
3. **Red-teaming through voting.** Fresh agents evaluating proposals
   they did not write catch blind spots the proposer missed.

Do NOT treat convergence ratios as statistical significance. 7/10
agents choosing the same approach means "this is the obvious answer
given the prompt," not "this is objectively correct."

### The Cycle

Every decision follows this loop:

```
  0. FRAME → 1. PARALLEL PROPOSALS → 2. SYNTHESIS →
  3. VOTE → 4. DECIDE or REPEAT → 5. IMPLEMENT + VERIFY
```

- **Phase 0 (Frame):** Use the framing guidance in the main body.
  Choose cycle size: **full (10 agents)** for major decisions, **light
  (5 agents)** for narrower decisions. If an identity document exists,
  check its `Generated:` date and `Commit:` SHA against the current
  state — if the SHA is >20 commits behind HEAD or a prior decision
  changed Architecture Map or Invariants, update the identity with
  Post-Decision Updates (see `docs/identity_extraction.md`, Identity
  Lifecycle). If no identity exists, consider extracting one.
- **Phase 1 (Propose):** N agents independently produce proposals
  following the Proposal Format. They must NOT see each other's work.
- **Quality Gate:** Score each proposal against QG-1 through QG-5.
  5/5 pass → include. 3-4 pass → return for one retry. 0-2 → reject.
  If >50% score 0-1, revise the framing.
- **Phase 2 (Synthesize):** A single synthesizer groups proposals,
  builds the comparison matrix, identifies convergence/divergence, and
  extracts novel ideas. The synthesizer must NOT propose new solutions.
  If synthesis reveals 80%+ convergence (8/10 full, 4/5 light), skip
  voting — convergence IS the vote.
- **Phase 3 (Vote):** N fresh agents score each approach on criteria.
  A "strong win" is 70%+. A "weak win" is 50-69%. A "split" is <50%.
- **Phase 4 (Decide):** Two-factor rule: sufficient convergence AND
  meets all success criteria. See tie-breaking and veto rules in the
  main body. Hard limit: 3 iterations per question.
- **Phase 5 (Implement):** See Migration Planning in the main body.

### Role Assignment Tables

Each role corresponds to one of the four lenses described in
[Thinking Through Multiple Lenses](#thinking-through-multiple-lenses).
The directives below are self-contained prompts for agents — they
restate the lens as an actionable constraint.

**Proposal roles — full cycle (10 agents):**

| Role | Agents | Directive (appended to prompt) |
|------|--------|-------------------------------|
| **Unconstrained** | 1-2 | No additional directive. Baseline agents that follow their own judgment. |
| **Minimalist** | 3-4 | "Your proposal MUST have the fewest new abstractions. If the problem can be solved by modifying existing code without new classes, protocols, or registries, do that." |
| **Extensibility-first** | 5-6 | "Your proposal MUST make the solved problem configurable by end users via `.ralph.toml` or mounted files, with zero code changes required for the most common customization." |
| **Migration-conservative** | 7-8 | "Your proposal MUST NOT break any existing PRD format, state file, or workflow. If a breaking change is unavoidable, provide an automatic migration that runs on first load." |
| **Contrarian** | 9-10 | "Before proposing a solution, explain concretely why the current design might actually be sufficient. If you still propose a change, it must address a limitation you cannot solve by adjusting usage patterns." |

**Proposal roles — light cycle (5 agents):**

| Role | Agents | Directive |
|------|--------|-----------|
| **Unconstrained** | 1 | No additional directive. |
| **Minimalist** | 2 | Same as full cycle. |
| **Extensibility-first** | 3 | Same as full cycle. |
| **Migration-conservative** | 4 | Same as full cycle. |
| **Contrarian** | 5 | Same as full cycle. |

With 5 agents there is no redundancy per role. If any proposal fails
the quality gate, you lose that perspective — consider re-running that
single agent.

**Voting roles — full cycle (10 agents):**

| Role | Agents | Directive |
|------|--------|-----------|
| **Balanced** | 1-4 | No additional directive. Score each criterion independently. |
| **Skeptical** | 5-7 | "Before scoring, identify the strongest objection to each approach. Your scores MUST reflect whether that objection is addressed." |
| **Pragmatic** | 8-10 | "Weight your scores toward what can be implemented and tested in the fewest changes. Penalize approaches that require coordinated changes across 5+ files." |

**Voting roles — light cycle (5 agents):**

| Role | Agents | Directive |
|------|--------|-----------|
| **Balanced** | 1-2 | No additional directive. |
| **Skeptical** | 3-4 | Same as full cycle. |
| **Pragmatic** | 5 | Same as full cycle. |

### Voting Rules and Convergence Thresholds

- Agents MUST justify every score with a specific reason.
- Agents MUST read the relevant source files before voting.
- Agents MUST compute weighted totals (see Evaluation Criteria).
- **Strong win:** 70%+ (7/10 full, 4/5 light). Adopt.
- **Weak win:** 50-69% (5-6/10 or 3/5). Adopt with validation plan.
- **Split:** <50%. Refine the question (see below).

**Veto rule:** If any criterion scores 1 from 3+ voting agents, that
proposal has a fatal flaw on that dimension. It cannot win regardless
of total score. Resolution:
1. Check whether the next-best proposal is veto-free. If yes, it
   becomes the winner (as a "weak win" requiring validation).
2. If all proposals are vetoed, the synthesizer constructs a patched
   version addressing the vetoed criterion. Runoff vote: patched
   version vs. next-best veto-free alternative.
3. If patching is not feasible, escalate to the human operator.

**Tie-breaking rules:**
- **Even split (5/5 or equivalent):** Compare weighted score totals.
  Higher total wins. If within 2%, the approach scoring higher on
  Migration Safety wins (hardest to fix retroactively).
- **Three-way split (no approach above 40%):** Eliminate the lowest-
  voted approach. Run a runoff vote on the remaining 2 — no new
  proposals, just a re-vote on a narrower choice.

### Question Refinement Protocol (on Split)

1. Decompose the original question into 2-3 sub-questions, each
   independently answerable and touching fewer files.
2. Each sub-question goes through the cycle independently. Results
   are composed into a unified proposal by the synthesizer.
3. If decomposition is impossible (the question is atomic), the next
   cycle uses a forced binary choice between the top 2 approaches.

Each iteration MUST use a refined framing — never repeat the same
question unchanged.

### Process Anti-patterns

**P1: Analysis paralysis — endless voting with no convergence.**
If a vote splits, do NOT re-run with the same framing.
> *Instead:* Refine into smaller sub-questions. Default to the
> proposal with the highest Simplicity score after 3 iterations, or
> escalate.

**P2: Bike-shedding — equal deliberation on unequal decisions.**
Not every question deserves 10 agents. Classify before spawning:
high-impact → full cycle; medium → 3-5 agents; low-impact → human
decision, no vote.

**P3: Scope creep — Q1 becomes a redesign of Q3.**
Each question has a defined boundary. Use "Downstream Impact" to flag
cross-question dependencies without solving them.

**P4: Anchoring on the majority.**
Evaluate each proposal on its own merits. Convergence is evidence,
not proof.
> *Instead:* Score all proposals before looking at convergence ratios.

**P5: Voting without reading code.**
Agents that vote based only on proposals (without reading source
files) miss practical issues.
> *Instead:* Open the key files each proposal changes and verify
> claims.

### Operator Notes

**Spawning agents with Claude Code:**

Each agent is a separate `claude` CLI invocation with no shared
conversation state.

```bash
# Phase 1 — parallel proposal agents
# IDENTITY is optional; use /dev/null if not extracted
IDENTITY=${IDENTITY:-/dev/null}
for i in $(seq 1 10); do
  cat "$IDENTITY" phase1-prompt.md role-$i.txt > /tmp/prompt-$i.md
done
for i in $(seq 1 10); do
  claude -p "$(cat /tmp/prompt-$i.md)" \
    --output-file docs/decisions/working/q1/agents/agent-$(printf '%02d' $i)-proposal.md \
    --max-turns 10 \
    --model opus &
done
wait

# Phase 3 — parallel voting agents
# Use trimmed identity (Invariants + Architecture Map only)
IDENTITY_TRIMMED=${IDENTITY_TRIMMED:-/dev/null}
for i in $(seq 1 10); do
  cat "$IDENTITY_TRIMMED" phase3-prompt.md voter-role-$i.txt > /tmp/vote-prompt-$i.md
done
for i in $(seq 1 10); do
  claude -p "$(cat /tmp/vote-prompt-$i.md)" \
    --output-file docs/decisions/working/q1/votes/voter-$(printf '%02d' $i).md \
    --max-turns 5 \
    --model sonnet &
done
wait
```

Agents should be instructed not to write files. Include in the
prompt: "You are in read-only mode. Do NOT create, modify, or delete
any files. Output your proposal as text only."

**Prompt composition order:** Identity appears first to establish
factual grounding before task instructions. For the full rationale
and per-tier variations, see `docs/identity_extraction.md`, section
"Prompt Composition Order."

**Timeouts:**
- Phase 1 (propose): 15 minutes per agent.
- Phase 2 (synthesize): 20 minutes.
- Phase 3 (vote): 10 minutes per agent.

Kill agents that exceed the timeout. Minimum to proceed:

| Phase | Full cycle | Light cycle |
|-------|-----------|-------------|
| Phase 1 → Phase 2 | 5 proposals | 3 proposals |
| Phase 3 → Phase 4 | 5 votes | 3 votes |

**Working directory layout:**

```
docs/decisions/
  working/
    q1/
      framing.md
      agents/
        agent-01-proposal.md
        ...
      quality-gate.md
      synthesis.md
      votes/
        voter-01.md
        ...
      tally.md
      constraint-addendum.md
    q2/
      ...
  DR-001-step-type-registry.md
  archive/
```

**Vote tallying:**

```markdown
## Vote Tally: Q1

### Raw Scores (per voter)

| Voter | Role | Winner | A weighted | B weighted | C weighted |
|-------|------|--------|-----------|-----------|-----------|
| 1 | Balanced | A | 4.05 | 3.65 | 3.20 |
| ... | | | | | |

### Aggregated Results

| Approach | Votes (winner) | Avg weighted score | Veto flags |
|----------|---------------|--------------------|------------|
| A | 7/10 | 3.92 | None |
| B | 3/10 | 3.65 | None |
| C | 0/10 | 3.18 | Migration Safety (4 voters scored 1) |

### Result: Strong win for A (70%)
### Success Criteria Check: [pass/fail per criterion]
### Decision: Adopt A / Adopt with validation / Refine
```

When using a tallying agent, give it an explicit directive: "You must
NOT re-evaluate the proposals. Your job is arithmetic: extract the
weighted totals from each vote document, compute averages, count
winners, check for vetoes, and report the result."

### Cost Estimates

For this codebase (~4.5K lines of Python, ~37K tokens of source):

| Scenario | Sonnet est. | Opus est. |
|----------|------------|----------|
| Per question (full cycle, with voting) | ~$1.10 | ~$5.50 |
| Per question (skip Phase 3 at 80%+ convergence) | ~$0.50 | ~$2.50 |
| Full 5-question cycle | ~$5.50 | ~$27.50 |
| Full cycle + 30% buffer | ~$7.00 | ~$36.00 |
| Identity extraction (one-time per cycle) | ~$0.15 | ~$0.75 |

*Buffer covers retries, quality gate re-runs, runoff votes, and spike
validations. Use Sonnet for Phase 3 voting to save ~40% per question.*

### Scaling Guidance

**Larger codebases (10K+ lines):**
- Phase 0 "Key Files" becomes critical — curate 3-7 files per
  question.
- Extract a repository identity document (see
  `docs/identity_extraction.md`). For 50K+ lines, extract per-module
  identities and include only the relevant one per question.
- Frame questions at the module level, not full-codebase level.

**More than 5 questions:**
- Group into clusters of 3-5 with dependency ordering. Complete one
  cluster before starting the next.
- Produce a consolidated constraint addendum per cluster to keep
  context manageable.

**Multi-dimensional questions:**
- If a question has more than 3 sub-dimensions, split it. Each
  sub-question should be independently decidable.
- Example: Q5 could split into Q5a (entry format and concurrency)
  and Q5b (prompt injection strategy), where Q5b depends on Q5a.

### Completion Criteria

The architecture redesign is complete when:

1. All design questions (Q1-Q5) have accepted decision records.
2. All implementation stories pass verification.
3. All post-implementation cleanup items (C1-C3) are done.
4. `uv run pytest` passes. `uv run pre-commit run -a` passes.
5. Each decision record has a retrospective note.

**Storage:** Decision records go in `docs/decisions/DR-NNN-<slug>.md`.
Number sequentially. Archive working documents in
`docs/decisions/archive/`.
