---
name: multi-agent
description: >
  Run the multi-agent codex process (FRAME → PROPOSE → DEBATE → VOTE → DECIDE)
  to make design decisions with diverse agent perspectives. Use when facing
  architectural decisions, design trade-offs, or any question that benefits
  from structured multi-perspective debate.
disable-model-invocation: true
---

## /multi-agent

Run a multi-agent decision process on a design question.

### Usage

Invoke with a question or topic:
```
/multi-agent How should we handle configuration?
```

### What happens

1. The question is framed into a structured decision format
2. Multiple agents with distinct identities propose solutions in parallel
3. Agents debate each other's proposals
4. Agents vote on the best approach
5. If no consensus, the cycle repeats with refined framing (up to 3 rounds)
6. A decision record is produced

### Execution

Run the orchestrator script:
```bash
ralph-run /opt/ralph/.claude/skills/multi-agent/orchestrate.py "$ARGUMENTS"
```

Results are written to `/workspace/run_ralph/multi-agent/<run_id>/`.
Present the contents of `decision.md` to the user when complete.
