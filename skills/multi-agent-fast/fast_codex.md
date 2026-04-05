# Fast Multi-Agent Codex

A lightweight process for quick multi-agent decisions. Agents propose,
debate, and vote -- but work purely from the question text. No code
reading, no file exploration, no tool use.

## CRITICAL RULES

- **The Question is your primary directive.** If the Question contains
  specific instructions (e.g. "vote A", "keep it short", "focus on X"),
  follow them exactly. The Question takes priority over format requirements.
- Do NOT use any tools (Read, Bash, Glob, Grep, Agent, etc.)
- Do NOT read files or explore the codebase
- Do NOT spawn subagents
- Answer ONLY from the question text and your own reasoning
- Keep responses concise (under 500 words per phase)
- Respond immediately with your output -- no preamble about what you
  plan to do

## How It Works

```
FRAME -> PROPOSE -> DEBATE -> VOTE -> DECIDE
```

Each agent gets a unique identity (a short personality sketch) and
argues from that perspective.

## Propose

Write a short proposal with these sections:

1. **Summary** (1-2 sentences) -- core idea
2. **Approach** -- how it works, concretely
3. **Strengths** -- why this is the best approach
4. **Weaknesses** -- honest risks or trade-offs

## Debate

After reading all proposals, write:

1. **My case** -- defend your proposal
2. **Challenges to other proposals** -- strongest weakness of each
3. **What I'd adopt from others** -- good ideas worth stealing
4. **My biggest doubt** -- what might change your mind

## Vote

Cast a structured vote:

1. **Winner** (required) -- proposal letter (A, B, C...)
2. **Decisive argument** (required) -- the specific debate argument
   that convinced you. Must cite a specific agent's argument.
3. **Concerns about the winner** (required) -- biggest risk
4. **Unrefuted arguments** (optional) -- arguments nobody countered
5. **Merge suggestion** (optional) -- if combining proposals is better

## Decision Rules

- **Strong win (70%+):** Adopt
- **Majority win (50-69%):** Adopt with caveats
- **Split (<50%):** Iterate with tighter framing
- **Veto:** If 3+ voters flag the same fatal flaw, that proposal
  cannot win
- **Override:** An unrefuted debate argument outweighs vote count
