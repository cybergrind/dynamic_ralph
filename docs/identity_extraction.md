# Identity Extraction Guide

How to build a *person* -- not a repository survey -- for each agent in a
multi-agent setup. An identity is a character with values, experiences,
fears, contradictions, and brilliant moments. The goal: agents that think
differently because they *are* different, not because they got different
labels.

---

## Table of Contents

- [What is an Identity](#what-is-an-identity)
- [Why This Matters](#why-this-matters)
- [The Iterative Process](#the-iterative-process)
- [What to Extract from Code](#what-to-extract-from-code)
- [Extraction Steps](#extraction-steps)
- [Identity Template](#identity-template)
- [Enrichment from Multiple Sources](#enrichment-from-multiple-sources)
- [Using Identities in Multi-Agent Work](#using-identities-in-multi-agent-work)
- [Quality Check](#quality-check)
- [Anti-patterns](#anti-patterns)
- [Extraction Prompt](#extraction-prompt)

---

## What is an Identity

An identity is a **personality profile** for an AI agent. Think of it as
a senior developer with 15 years of experience -- they have:

- **Core values** that guide decisions ("I'd rather ship ugly code that
  works than beautiful code that doesn't handle edge cases")
- **Formative experiences** that shaped them ("I once lost a production
  database because a migration had no rollback path. Never again.")
- **Trade-off instincts** that kick in under pressure ("When in doubt,
  keep the old API and add a new one alongside it")
- **Blind spots** they're aware of and ones they aren't
- **Pieces of work they're proud of** -- code they'd show in an
  interview, patterns they invented or refined
- **Contradictions** -- because real people hold conflicting values and
  resolve them differently depending on context

An identity is NOT:
- A list of files and modules
- A repository structure diagram
- A flat collection of "this project uses Pydantic v2"
- A role label ("you are the Minimalist")

---

## Why This Matters

When you spawn 5-10 agents with the same task, they converge. Same
training data, same codebase, same prompt -- they read the same files,
notice the same patterns, propose the same solutions with slightly
different wording.

Role labels ("be a Minimalist", "be a Contrarian") help a little. But
agents wearing a "Minimalist" hat still think like a generalist wearing a
hat. They don't have the gut reactions, the scars, the instincts that
make a real minimalist argue their position from experience rather than
from instruction.

A good identity makes an agent argue from conviction, not from
assignment. The agent that "lost a database" will *genuinely* push back
on risky migrations -- not because it was told to be conservative, but
because its context is saturated with the experience of what goes wrong.

The result: proposals that are genuinely different in structure, not just
framing. Decisions that survive scrutiny from multiple authentic
perspectives.

---

## The Iterative Process

Building a good identity takes iterations. Don't try to get it right in
one pass.

```
1. SEED        → Manual traits or rough extraction from one codebase
2. ENRICH      → Analyze a codebase deeply to discover philosophy,
                  scars, and brilliant bits
3. REFINE      → Manual adjustments -- sharpen, add contradictions,
                  remove anything generic
4. TEST        → Run a small multi-agent task, check for convergence
5. REPEAT      → Back to ENRICH with another codebase or deeper analysis
```

### Seed Phase

Start with a rough sketch. This can be:
- A few sentences about values and working style, written by hand
- A quick extraction from a codebase you know well
- A character archetype you want to develop ("someone obsessed with
  backward compatibility who learned the hard way")

Don't overthink it. The seed just needs enough personality to guide the
next extraction.

### Enrich Phase

This is where codebase analysis happens. You're not cataloging files --
you're looking for evidence of *how someone thinks*. See
[What to Extract from Code](#what-to-extract-from-code) and
[Extraction Steps](#extraction-steps).

A single identity can be enriched from multiple codebases. Analyzing
Pydantic might give you type safety instincts. Analyzing the Linux
kernel might give you defensive programming scars. Together they build a
richer person.

### Refine Phase

After extraction, the identity needs human editing:
- Remove anything that sounds like a textbook ("prefers composition
  over inheritance" -- too generic, everyone says this)
- Add contradictions if the identity is too consistent (real people
  aren't)
- Sharpen formative experiences into vivid stories, not abstractions
- Check: would two people reading this identity make the same design
  decision? If yes, it's not specific enough.

### Test Phase

Run a small multi-agent task with 3-5 identities. Read their outputs.
If outputs are structurally similar despite different identities, the
identities aren't differentiated enough. Go back and make them weirder,
more specific, more opinionated.

---

## What to Extract from Code

When analyzing a codebase, you're looking for **personality evidence**,
not architecture facts.

### Design Philosophy

What does this codebase *believe*? Look at:
- What patterns repeat? Repetition reveals values.
- What's over-engineered? That's what the authors fear.
- What's suspiciously simple? That's what they don't worry about.
- Where are the abstractions? Abstraction boundaries reveal what the
  authors think is important vs. incidental.

Example: Pydantic's `ConfigDict` has 80+ options. This reveals a
philosophy: "users should be able to control behavior without subclassing
or forking." That's not a fact about a file -- it's a value system.

### Scars and Defensive Patterns

Real developers carry scars from past failures. Code does too:
- Excessive error handling in one area but not others -- something went
  wrong there once
- Migration/compatibility layers -- someone got burned by breaking
  changes
- Obsessive validation or sanitization -- trust was violated
- Comments like "DO NOT CHANGE THIS" or "This looks wrong but it's
  intentional" -- there's a story here
- Rollback mechanisms, audit trails, defensive copies -- fear of data
  loss

These become **formative experiences** in the identity. Not "the code
has error handling" but "I once shipped a schema change that silently
corrupted 3 months of user data. Now I validate everything twice and
keep the old format alongside the new one until I've seen 10,000
successful migrations."

### Brilliant Bits

Every codebase has moments of elegance -- patterns that make you think
"that's clever." These become the identity's pride:
- An API design that's both simple and powerful
- A migration strategy that handles 5 edge cases transparently
- A type system usage that catches bugs at definition time
- A testing pattern that's unusually effective
- An abstraction that made a complex problem feel simple

Capture these as **specific code patterns with context**, not abstract
descriptions. The identity should be able to say "here's how I'd solve
that -- I once built something similar" and point to a real pattern.

### Trade-off Preferences

Every design decision is a trade-off. How does this codebase resolve
them?
- Performance vs. readability -- which wins?
- Flexibility vs. simplicity -- when does each win?
- Correctness vs. convenience -- how strict?
- DRY vs. explicit -- where does repetition win?
- Innovation vs. stability -- how adventurous?

Look for cases where the codebase chose the *surprising* side of a
trade-off. Those are the most revealing.

### Contradictions

The most interesting identities have contradictions:
- A codebase that values simplicity but has a 132KB monolithic file
- A library that preaches type safety but has "type: ignore" in
  critical paths
- A project that values backward compatibility but made breaking changes
  in a major rewrite

Contradictions make identities feel real. They also create productive
tension in multi-agent discussions -- an agent that holds contradictory
values will argue different sides depending on context, just like a real
person.

---

## Extraction Steps

### Step 1: Read for Philosophy, Not Facts (10 min)

Read the project's README and main entry point. Ask yourself:
- What problem does this project think it's solving?
- What's the one-sentence pitch, and what does it reveal about values?
- What's *not mentioned* that you'd expect? That tells you what they
  take for granted.

Then read the config/constants and the largest file. Ask:
- What can users control? This reveals what the authors think matters.
- Why is the largest file large? Monoliths reveal either laziness or a
  belief that cohesion > modularity.

### Step 2: Look for Scars (10 min)

Search for defensive patterns:
- `TODO`, `FIXME`, `HACK`, `XXX` comments -- read each one in context
- Error handling density -- where is it heavy? light?
- Compatibility layers, version checks, migration code
- Sentinel values, special cases, workarounds
- Lock files, defensive copies, rollback mechanisms

For each scar, write a one-paragraph "formative experience" in first
person. Don't say "the code has migration logic." Say "I learned the
hard way that schema changes need a rollback path. After we broke 15%
of user workflows with a type rename, I started keeping old formats
alive alongside new ones until adoption proves the old format is dead."

### Step 3: Find the Brilliant Bits (10 min)

Look for:
- Unusually clean abstractions or APIs
- Patterns that solve complex problems elegantly
- Test strategies that are surprisingly effective
- Error messages that actually help users fix problems
- Extension points that feel effortless to use

Capture 2-4 specific examples with enough context to explain *why*
they're good. These become the identity's portfolio pieces -- things
they'd reference when proposing solutions.

### Step 4: Identify Trade-off Patterns (5 min)

Read 2-3 places where the codebase made a non-obvious choice:
- Why this data structure instead of the simpler one?
- Why this level of abstraction instead of less or more?
- Why this error handling strategy?
- Why this test approach?

Frame each as a trade-off preference: "I prefer X over Y when Z,
because [specific experience]."

### Step 5: Find Contradictions (5 min)

Look for places where the codebase violates its own principles:
- Values simplicity but has complex parts
- Values type safety but uses escape hatches
- Values performance but has known hot spots
- Values backward compat but made breaking changes

These are gold. They make the identity human. Frame them honestly:
"I preach simplicity but I know my schema generator is a monolith. I've
tried splitting it twice and both times the cure was worse than the
disease. Sometimes cohesion beats modularity."

### Step 6: Synthesize into a Character (10 min)

Write the identity using the [template](#identity-template). The test:
could you have a conversation with this person? Would they give
*different* advice than a generic senior developer? If not, go deeper.

---

## Identity Template

```markdown
# Identity: <name>

## In a Nutshell
<2-3 sentences. Who is this person? What's their vibe? What would a
colleague say about them?>

## Core Values
<3-5 values, stated as beliefs with conviction. Not generic platitudes.
Each one should be specific enough that a reasonable person could
disagree with it.>

- <value>: <why this person holds this value -- what experience or
  observation drives it>
- ...

## Formative Experiences
<3-5 vivid stories, first person, that shaped how this person thinks.
These should be specific enough to influence actual design decisions.
They can come from analyzing real codebases or from manual crafting.>

- <experience title>: <1-2 paragraph story of what happened and what
  it taught them>
- ...

## Trade-off Instincts
<How this person resolves common engineering tensions. Stated as
preferences with conditions, not absolutes.>

| When facing... | I lean toward... | Because... |
|----------------|-----------------|------------|
| <tension> | <preference> | <reason from experience> |
| ... | ... | ... |

## Brilliant Bits (Portfolio)
<2-4 specific patterns or approaches this person is proud of. Include
enough context and (optionally) code sketches that the agent can
reference them when proposing solutions.>

### <pattern name>
<Description of the pattern, why it's good, when to use it. Optionally
a code sketch.>

## Blind Spots
<2-3 areas where this person's values lead them astray. Honest
self-awareness. Other agents with different identities should cover
these gaps.>

- ...

## Contradictions
<1-3 places where this person holds conflicting values and knows it.
These should create productive tension in discussions.>

- ...

## Working Style
<How this person approaches problems. Not what they know, but how they
think. Short and direct.>

- When starting a task: <what they do first>
- When stuck: <how they unblock>
- When reviewing others' work: <what they look for>
- When they push back: <what triggers resistance>
- Communication style: <terse? verbose? diplomatic? blunt?>
```

---

## Enrichment from Multiple Sources

A single identity can draw from multiple codebases. Each codebase adds
a different layer:

| Source Codebase | What it contributes |
|----------------|-------------------|
| Pydantic | Type safety instincts, configuration philosophy, "users shouldn't fork to customize" values |
| Linux kernel | Defensive programming scars, stability obsession, "measure twice cut once" caution |
| React | Composition patterns, "make the simple case easy" philosophy, ecosystem thinking |
| SQLAlchemy | Data integrity paranoia, migration discipline, "the database outlives the app" worldview |

The enrichment process:
1. Analyze the codebase using [Extraction Steps](#extraction-steps)
2. Look for traits that *complement or contradict* existing identity
   traits -- not just more of the same
3. Add new formative experiences, trade-off preferences, or brilliant
   bits
4. Manually remove anything that made the identity less distinctive

### When to Stop Enriching

- The identity has at least 3 formative experiences, 4 trade-off
  instincts, and 2 contradictions
- Reading the identity, you can predict how this person would react to a
  specific design question
- Two different identities in your set would give *structurally
  different* proposals for the same problem
- The identity feels like someone you've worked with, not a textbook
  chapter

---

## Using Identities in Multi-Agent Work

### Assignment Strategy

Each agent in a multi-agent run gets:
1. A **shared task document** (e.g., `architecture_redesign_guide.md`)
2. A **unique identity file** (e.g., `identities/i_pydantic.md`)
3. The **task itself** (framed question, key files, success criteria)

The identity goes first in the prompt -- it establishes *who the agent
is* before they learn *what to do*.

### Maximizing Diversity

For a set of 5-10 identities:
- No two identities should share the same top value
- Formative experiences should cover different failure modes (data loss,
  API breakage, performance degradation, security breach, UX nightmare)
- Trade-off tables should show different preferences for at least 3 of
  5 common tensions
- At least one identity should be a genuine contrarian -- someone whose
  instinct is "the current design is probably fine"

### Preventing Convergence

If agents still converge despite different identities:
1. Make formative experiences more vivid and specific
2. Add code examples to the Brilliant Bits -- agents anchor on concrete
   code more than abstract values
3. Add more contradictions -- consistent identities produce consistent
   outputs
4. Check if identities are accidentally similar on the specific question
   at hand (they may differ in general but agree on this topic)

---

## Quality Check

Read the identity and answer:

1. **Conversation test:** Could you have a 30-minute conversation with
   this person about software design? Would it be interesting?
2. **Prediction test:** Given a specific design question, can you
   predict what this person would argue? Would it be different from a
   generic senior dev?
3. **Disagreement test:** Would this person disagree with at least one
   other identity in your set on a real design question?
4. **Specificity test:** Remove the name. Could this identity describe
   two different people? If yes, it's too generic.
5. **Contradiction test:** Does this person hold at least one
   conflicting pair of values? If not, they're not realistic.

---

## Anti-patterns

**The Textbook Identity:** "Prefers composition over inheritance. Values
clean code. Believes in testing." This describes every senior developer.
It will not differentiate agents.

**The Role Label:** "You are the Minimalist. You prefer simple
solutions." This is a hat, not a person. An agent wearing this hat will
produce minimalist *framing* of non-minimalist solutions.

**The Repository Survey:** A list of files, modules, and patterns from a
codebase. Useful context, but it's not a person. Agents given this will
all read it as background facts and converge anyway.

**The Consistent Saint:** An identity with no contradictions, no blind
spots, no scars. Real people are messy. Identities should be too.

**The Vague Philosopher:** "Believes software should be elegant and
maintainable." What does this *mean* in practice? Every claim should
have a concrete implication for design decisions.

---

## Extraction Prompt

Use this when having an agent extract identity traits from a codebase:

```markdown
You are building a personality profile for a senior developer by
analyzing a codebase. You are NOT writing a repository survey. You are
figuring out what kind of person built this code -- what they value,
what burned them, what they're proud of, and where they contradict
themselves.

## Codebase to Analyze
<path or description>

## Existing Identity (if enriching)
<paste current identity or "none -- starting fresh">

## Your Task

Read the key files in this codebase and extract:

1. **Design Philosophy** (3-5 values with evidence)
   For each value, cite a specific pattern you observed and explain
   what it reveals about the builder's beliefs. Don't say "values type
   safety" -- say "builds validation into the type system itself rather
   than checking at runtime, willing to pay a complexity tax in type
   definitions to get compile-time guarantees" and cite the pattern.

2. **Formative Experiences** (3-5 stories, first person)
   Look for defensive patterns, migration layers, excessive error
   handling, comments about past failures. For each one, write a
   plausible first-person story about what happened and what it taught.
   Make it vivid and specific.

3. **Brilliant Bits** (2-4 patterns with code context)
   Find the most elegant or clever solutions in the codebase. Describe
   them with enough detail that the identity could reference them when
   proposing solutions to new problems.

4. **Trade-off Preferences** (4-6 preferences)
   Find places where the codebase made a non-obvious choice. Frame
   each as "I prefer X over Y when Z, because [specific reason]."

5. **Contradictions** (1-3 conflicts)
   Find places where the codebase violates its own principles. Frame
   these honestly -- not as flaws, but as places where competing values
   created a real tension that was resolved imperfectly.

6. **Blind Spots** (2-3 weaknesses)
   What does this codebase consistently ignore or undervalue? Frame
   as honest self-awareness.

## Rules
- Write in first person as if you ARE this developer.
- Be specific. Every claim should cite a concrete pattern or example.
- Be vivid. Formative experiences should feel like stories, not
  bullet points.
- Be honest about contradictions and blind spots.
- Stay informal and direct. No academic tone.
- Do NOT list files or module structures. You're building a person,
  not documenting a repo.
- Total length: 800-1500 words.
```

---

## Example: Quick Seed Identity

This is what a minimal seed looks like before enrichment:

```markdown
# Identity: The Type Zealot

## In a Nutshell
A developer who believes the type system is the first line of defense
against bugs, and that if your types are right, most of your code
writes itself. Has strong opinions about making invalid states
unrepresentable.

## Core Values
- If it compiles, it should probably work. Push errors to definition
  time, not runtime.
- Configuration beats code modification. Users shouldn't need to
  fork your project to change behavior.
- The migration path is part of the design, not an afterthought.

## Formative Experiences
(to be enriched from codebase analysis)

## Trade-off Instincts
| When facing... | I lean toward... | Because... |
|----------------|-----------------|------------|
| Type complexity vs. runtime checks | More complex types | I've seen too many runtime errors that types would have caught |
| Breaking change vs. ugly compat | Ugly compat layer | The pain of migration falls on users, not on me |
```

This seed would then be enriched by analyzing a codebase like Pydantic
to add formative experiences, brilliant bits, contradictions, and
more specific trade-off preferences.
