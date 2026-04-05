---
name: multi-agent-fast
description: >
  Lightweight multi-agent decision process. Skips code reading and deep analysis.
  Runs the full voting pipeline (propose/debate/vote/tally/decide) on the question
  text alone. Completes in under 1 minute for simple questions.
disable-model-invocation: true
---

## /multi-agent-fast

Run a fast multi-agent decision process -- no code reading, no file exploration.

### Usage

Invoke with a question or topic:
```
/multi-agent-fast Should we use polling or webhooks?
```

### What happens

1. The question is framed into a structured decision format
2. Three agents with distinct personalities (pragmatist, architect, skeptic) propose solutions in parallel
3. Agents debate each other's proposals
4. Agents vote on the best approach
5. If no consensus, the cycle repeats (up to 2 rounds)
6. A decision record is produced

### Execution

Run the orchestrator script:
```bash
ralph-run /opt/ralph/.claude/skills/multi-agent-fast/orchestrate.py "$ARGUMENTS"
```

Results are written to `/workspace/run_ralph/multi-agent/<run_id>/`.
Present the contents of `decision.md` to the user when complete.
