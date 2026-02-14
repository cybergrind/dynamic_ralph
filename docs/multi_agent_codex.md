# Multi-Agent Codex

A process for getting genuinely different agents to work together on
hard problems. Each agent has a unique identity (values, scars, instincts)
and argues from conviction, not from assignment. The process is
iterative: propose, debate, vote, refine -- until the best idea wins on
merit, not on popularity.

**Companion documents:**
- `docs/identity_extraction.md` -- How to build identity files for agents.
- `docs/identities/` -- Identity files ready to use.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Framing the Task](#framing-the-task)
- [Round 1: Propose](#round-1-propose)
- [Round 2: Debate](#round-2-debate)
- [Round 3: Vote](#round-3-vote)
- [Iteration and Convergence](#iteration-and-convergence)
- [Multi-Question Workflows](#multi-question-workflows)
- [Anti-patterns](#anti-patterns)
- [Operator Guide](#operator-guide)
- [Output Formats](#output-formats)

---

## How It Works

```
FRAME → PROPOSE → DEBATE → VOTE → DECIDE or ITERATE
          ↑                           |
          └───────────────────────────┘
```

Each agent gets:
1. **This codex** -- the shared rules of engagement.
2. **A unique identity** -- who they are, what they value, what burned
   them (see `docs/identity_extraction.md`).
3. **The task** -- framed question, scope, success criteria, key files.

The identity goes first in the prompt. It establishes *who the agent is*
before they learn *what to do*. An agent with the "Runtime Engineer"
identity will naturally focus on concurrency and performance. An agent
with the "Careful Pragmatist" identity will naturally focus on failure
modes and safe defaults. This differentiation comes from conviction,
not from a one-sentence role directive.

### Why Not Just Vote?

Pure voting produces mediocre consensus. The best idea often comes from
one agent who sees something the others miss -- but in a straight vote,
that agent gets outnumbered by the majority who converged on the obvious
answer.

The debate round fixes this. Before voting, every agent must *argue*
for their approach and *challenge* the alternatives. A proposal that
can't survive scrutiny from 4 different perspectives probably isn't
good enough. A proposal that one agent defends brilliantly against all
challengers might be the right answer even if it's unconventional.

**The smartest argument wins, not the most popular one.** Voters must
justify their choice by citing specific arguments from the debate, not
just scores.

---

## Framing the Task

Before anyone proposes anything, frame the question precisely. Poor
framing produces proposals that solve different problems.

### The Frame

```
QUESTION: <one sentence, no preferred answer embedded>

SCOPE:
  In scope:
    - <item>
  Out of scope:
    - <item>

SUCCESS CRITERIA:
  1. <measurable criterion -- how you know the problem is solved>
  2. ...

KEY FILES (3-7):
  - <path/to/file> -- <why this file matters>

CONSTRAINTS:
  - <non-negotiable rules proposals must respect>

IDENTITIES: <list of identity files to assign to agents>
```

### Framing Rules

- **Neutral framing.** The question must NOT contain a preferred answer.
  Write "How should we handle configuration?" not "Configuration is
  broken -- should we add a TOML file?"
- **One question, one decision.** If you can't state it in one sentence,
  split it. Two tangled questions produce proposals that solve different
  subsets.
- **Success criteria are mandatory.** Without them, you can't tell if
  the winning proposal actually works. Write criteria that you can
  verify after implementation.
- **Constraints are filters, not preferences.** A constraint eliminates
  proposals. A preference is what the debate is for.

---

## Round 1: Propose

Each agent independently produces a proposal. They must NOT see each
other's work.

### What Goes Into a Proposal

Every proposal must contain:

1. **Summary** (2-3 sentences) -- What's the core idea? What problem
   does it solve? Cite a specific current limitation.
2. **Code sketch** -- Concrete type definitions, function signatures,
   config schemas. Not prose. Not pseudocode. Not 200 lines of
   implementation. Enough to evaluate the design.
3. **Files changed** -- Which files are affected and what changes in
   each.
4. **Migration plan** -- What breaks, how to auto-migrate, how old
   formats continue to work. If nothing breaks, say so.
5. **What I'd argue** -- The strongest case for this approach. This is
   the seed for the debate round. Write it like you're selling this to
   skeptics.
6. **What worries me** -- The honest weaknesses. Where might this fail?
   What trade-off are you making? This builds trust and helps voters
   assess risk.

### What NOT to Put in a Proposal

- Alternatives considered. That's for the debate round.
- Weighted scoring of your own proposal. Self-assessment is unreliable.
- Generic trade-offs ("this adds some complexity"). Be specific.

### Quality Gate

A proposal passes if it has:
- [ ] Actual code (type definitions, signatures) -- not just prose
- [ ] Specific file paths from the codebase
- [ ] A migration plan (even if it's "nothing breaks")
- [ ] A "What I'd argue" section with a genuine case
- [ ] A "What worries me" section with honest concerns

If a proposal fails 3+ checks, send it back for revision. If >50% of
proposals fail, the framing is probably bad -- reframe the question.

---

## Round 2: Debate

This is the key round. Each agent reads ALL proposals and writes a
debate document arguing for their own approach and challenging the
alternatives. This is where the "smartest guy" gets to make their case.

### What Goes Into a Debate Entry

Each agent writes:

1. **My case** -- Defend your proposal. Reference your identity's values
   and experiences. Explain why your approach handles the success
   criteria better than alternatives. Be specific: "Proposal B uses a
   registry pattern, but as someone who's maintained a 132KB monolith
   that grew from a registry, I can tell you registries accumulate
   complexity silently."

2. **Challenges to other proposals** -- For each alternative, identify
   the strongest weakness. Not nitpicks -- the real risk. Reference your
   experience. "Proposal A's migration path assumes all state files
   have a version field. I've shipped migrations that made that
   assumption, and 15% of real-world files didn't have it."

3. **What I'd adopt from others** -- If another proposal has an idea
   worth stealing, say so. This isn't weakness -- it's honesty, and it
   helps the voting round construct hybrids.

4. **My biggest doubt** -- The one thing that might make you change your
   mind. This is the strongest signal for voters.

### Debate Rules

- **Argue from experience, not from principle.** "Simplicity is better"
  is a principle. "I maintained a system with 80+ config options and it
  overwhelmed new users" is experience. Identity files provide the
  experiences -- use them.
- **Attack the proposal, not the agent.** "This approach risks data
  loss during migration" not "this agent doesn't understand migrations."
- **Concede when appropriate.** If another proposal genuinely handles
  something better, say so. Voters trust agents who concede on small
  points and hold firm on big ones.
- **Be specific.** Reference file paths, function names, concrete
  scenarios. Vague arguments get ignored.

---

## Round 3: Vote

Fresh agents (or the same agents with full debate context) score each
approach and pick a winner. Critically: votes must cite arguments from
the debate, not just evaluate proposals in isolation.

### What Goes Into a Vote

Each voter writes:

1. **Winner** -- Which approach they'd adopt.
2. **Decisive argument** -- The specific argument from the debate that
   was most persuasive. "Agent 3's point about migration failures in
   files without version fields convinced me that Proposal A's
   migration path is too risky."
3. **Concerns about the winner** -- Even if you voted for it, what's
   the biggest risk?
4. **Merge suggestion** (optional) -- If the best outcome combines
   elements from multiple proposals, describe the hybrid.

### Decision Rules

**Strong win (70%+):** Adopt. The debate produced consensus.

**Majority win (50-69%):** Adopt with a validation plan. After
implementation, verify the concerns raised by the minority. If they
were right, revisit.

**Split (<50%):** The question needs refinement. Either:
- The question is actually two questions -- split it.
- The framing was ambiguous -- proposals solved different problems.
- The proposals were too similar -- the debate didn't surface real
  differences. Make identities more extreme.

**Veto rule:** If 3+ voters flag the same fatal flaw in a proposal
(regardless of its vote count), it cannot win. The next-best
alternative wins, or the flaw is patched and re-voted.

**Override rule:** If one agent made an argument in the debate that NO
voter was able to counter, and the winner contradicts that argument,
escalate. An unrefuted argument is stronger than a vote count. This is
the "smartest guy" principle in action.

---

## Iteration and Convergence

One round is often not enough. The first round surfaces the landscape;
the second round sharpens the real disagreements; the third round
usually converges.

### When to Iterate

- **Split vote** -- Reframe into sub-questions or forced binary choice.
- **New information** -- The debate revealed a constraint nobody
  considered. Add it to the frame and re-run.
- **Hybrid emerged** -- Voters suggested a combination that didn't exist
  as a proposal. Run a focused round on the hybrid vs. the original
  winner.

### When to Stop

- **Strong win with no unrefuted counter-arguments.** Done.
- **3 iterations reached.** If nothing converges after 3 rounds, the
  question may be underdetermined. Adopt the proposal with the best
  migration safety (hardest to fix retroactively) or escalate to a
  human.
- **Everyone agrees in the debate.** If agents concede to each other
  and converge before voting, skip the vote. Agreement is agreement.

### Refinement Between Rounds

Each iteration MUST change something:
- Tighter framing (narrower scope, clearer success criteria)
- New constraint (from something the debate revealed)
- Forced binary choice (top 2 only, no new proposals)
- Different identities (if proposals were too similar)

Never re-run the same question with the same framing. That's not
iteration -- it's hoping for a different random outcome.

---

## Multi-Question Workflows

For complex projects with multiple design decisions, order matters.
Earlier decisions constrain later ones.

### Dependency Ordering

Group questions into rounds by dependency:
```
Round 1: Q1 and Q2 (independent -- run in parallel)
Round 2: Q3 (depends on Q1)
Round 3: Q4 and Q5 (depend on Q3, independent of each other)
```

Questions within the same round are independent and CAN run in parallel
(different agent groups working simultaneously).

### Constraint Propagation

After each decision, produce a constraint addendum for all subsequent
work:

```
## Constraint Addendum: <question title>

### Decision Summary
<1 paragraph -- what was decided>

### Interface Contracts
Subsequent proposals MUST conform to:
  <concrete signatures, schemas, or data shapes>

### Ruled-Out Approaches
  - <approach>: <why it's now incompatible>
```

Later agents receive all prior constraint addenda as part of their
framing. Proposals that violate prior decisions are disqualified unless
they include a compelling argument to revisit.

### Virtual Diffs

When a decision is adopted but not yet implemented, later proposals
must design against the *future* state. Provide "virtual diffs" showing
the adopted code that doesn't exist yet, labeled clearly. Proposals
referencing superseded code are flagged.

---

## Anti-patterns

### Design Anti-patterns

Proposals that fall into these traps should be challenged hard in the
debate round.

**Over-abstraction.** Don't add a registry, a factory, a protocol, AND
a plugin system for the same concept. Pick one mechanism. If you're
creating more than one new file for extensibility on a single concept,
you're over-engineering.

**Premature generalization.** Don't design for "any future X" when
there's one X. Name the specific second case your design handles. If
you can't name one, simplify.

**Config explosion.** Don't expose a config knob for every internal
constant. For every config option, cite the specific user need. No
citation, no option.

**Breaking changes for aesthetics.** Don't rename types for cleanliness
alone. Include an auto-migration path, or keep the old name as an
alias.

**Solving hypothetical problems.** Every proposal must cite a concrete
current limitation, not "what if someone wants to..."

**Local optimization.** A brilliant solution to question 1 that makes
question 3 impossible is a net negative. State the downstream impact.

### Process Anti-patterns

**Analysis paralysis.** If a vote splits, do NOT re-run with the same
framing. Refine into smaller sub-questions. Default to the safest
option after 3 iterations.

**Bike-shedding.** Not every question needs 10 agents. High-impact
decisions get full cycles. Medium decisions get 3-5 agents. Low-impact
decisions get a human call, no vote.

**Anchoring on the majority.** Evaluate each proposal on merit before
looking at vote counts. The debate arguments matter more than the tally.

**Debating without reading code.** Agents that argue based only on
proposals without reading the actual source files miss practical issues.
Every debater must cite specific code from the key files.

**Identical proposals despite different identities.** If agents
converge despite genuinely different identities, the question has an
obvious answer (great -- just adopt it) or the framing is too narrow
to allow divergence.

---

## Operator Guide

### Running the Process

Each agent is a separate CLI invocation with no shared conversation
state.

```bash
# Assign identities
IDENTITIES=(
  "docs/identities/i_pydantic.md"
  "docs/identities/i_httpx.md"
  "docs/identities/i_tokio.md"
)
CODEX="docs/multi_agent_codex.md"
TASK="path/to/framing.md"

# Round 1: Propose (parallel)
for i in "${!IDENTITIES[@]}"; do
  cat "${IDENTITIES[$i]}" "$CODEX" "$TASK" > /tmp/prompt-$i.md
  claude -p "$(cat /tmp/prompt-$i.md)" \
    --output-file "working/proposals/agent-$i.md" \
    --max-turns 10 &
done
wait

# Round 2: Debate (parallel, each agent sees ALL proposals)
ALL_PROPOSALS="working/proposals/all-proposals.md"
cat working/proposals/agent-*.md > "$ALL_PROPOSALS"
for i in "${!IDENTITIES[@]}"; do
  cat "${IDENTITIES[$i]}" "$CODEX" "$TASK" "$ALL_PROPOSALS" \
    > /tmp/debate-prompt-$i.md
  # Append debate instructions
  echo "Round 2: Read all proposals above. Write your debate entry." \
    >> /tmp/debate-prompt-$i.md
  claude -p "$(cat /tmp/debate-prompt-$i.md)" \
    --output-file "working/debate/agent-$i.md" \
    --max-turns 5 &
done
wait

# Round 3: Vote (parallel, each voter sees proposals + debate)
ALL_DEBATE="working/debate/all-debate.md"
cat working/debate/agent-*.md > "$ALL_DEBATE"
for i in "${!IDENTITIES[@]}"; do
  cat "${IDENTITIES[$i]}" "$CODEX" "$ALL_PROPOSALS" "$ALL_DEBATE" \
    > /tmp/vote-prompt-$i.md
  echo "Round 3: Read all proposals and debate above. Cast your vote." \
    >> /tmp/vote-prompt-$i.md
  claude -p "$(cat /tmp/vote-prompt-$i.md)" \
    --output-file "working/votes/agent-$i.md" \
    --max-turns 3 &
done
wait
```

### Prompt Composition Order

Order matters. Identity first -- it establishes who the agent is before
they learn the rules or the task.

```
1. IDENTITY            -- who you are (values, scars, instincts)
2. CODEX               -- how the process works (this document)
3. TASK FRAMING         -- what to solve (question, scope, criteria)
4. PRIOR PROPOSALS      -- (Round 2+) what others proposed
5. PRIOR DEBATE         -- (Round 3) how arguments played out
```

### Sizing

| Question complexity | Agents | Rounds | Approx. cost (Sonnet) |
|--------------------|--------|--------|----------------------|
| Narrow (1-3 files) | 3 | 1-2 | ~$0.50 |
| Medium (3-7 files) | 5 | 2-3 | ~$2.00 |
| Broad (7+ files, architectural) | 7-10 | 2-3 | ~$5.00 |

Use Sonnet for voting rounds to save cost. Use Opus for proposals and
debate when the question is complex.

### Timeouts

| Phase | Timeout |
|-------|---------|
| Propose | 15 min per agent |
| Debate | 10 min per agent |
| Vote | 5 min per agent |

### Minimum Quorum

| Phase | Full cycle (7+) | Light cycle (3-5) |
|-------|-----------------|-------------------|
| Proposals needed | 5 | 3 |
| Debate entries needed | 5 | 3 |
| Votes needed | 5 | 3 |

Kill agents that exceed the timeout. If you're below quorum, re-run the
missing agents rather than proceeding short-handed.

### Working Directory Layout

```
working/
  framing.md
  proposals/
    agent-0.md
    agent-1.md
    ...
    all-proposals.md
  debate/
    agent-0.md
    agent-1.md
    ...
    all-debate.md
  votes/
    agent-0.md
    agent-1.md
    ...
  tally.md
  decision.md
  constraint-addendum.md
```

---

## Output Formats

### Decision Record

The final output of a completed round:

```markdown
## Decision: <short title>

### Status: accepted | accepted-with-validation | escalated
### Date: <YYYY-MM-DD>
### Question: <the framed question>

### Decision
<2-3 sentences describing the chosen approach>

### Rationale
- Winning argument: <the specific debate argument that carried the day>
- Vote: <X/N agents, with dissent summary>
- Key risk: <the biggest concern and how it's mitigated>

### Code Contract
<Key type definitions, function signatures, and schemas from the
winning proposal. This is the binding spec for implementation.>

### Success Criteria
<Copied from framing -- used for verification after implementation.>

### Implementation Notes
- Start with: <file>
- Key constraint: <what implementers must not violate>
```

### Tally Format

```markdown
## Tally: <question>

### Votes
| Agent | Identity | Winner | Decisive Argument |
|-------|----------|--------|-------------------|
| 0 | Schema Architect | B | "Agent 2's point about migration risk..." |
| 1 | Careful Pragmatist | A | "Agent 0's code sketch showed..." |
| ... | | | |

### Result: <Strong/Majority/Split> for <approach>
### Unrefuted Arguments: <any debate argument no voter countered>
### Decision: Adopt / Adopt with validation / Iterate / Escalate
```

### Constraint Addendum

Produced after each decision in multi-question workflows:

```markdown
## Constraint Addendum: <question title>
Date: <YYYY-MM-DD>

### Decision Summary
<1 paragraph>

### Interface Contracts
Subsequent proposals MUST conform to:
  <concrete Python/Rust/etc. signatures or schemas>

### Ruled-Out Approaches
- <approach>: <why it's incompatible with this decision>

### Virtual Diff (if not yet implemented)
The following code does NOT exist yet but will replace the current
implementation. Design against this, not the current source.
  <adopted code sketch>
```
