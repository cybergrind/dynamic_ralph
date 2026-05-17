---
description: Delegate a bulk file Q&A to the cheap-model coworker.
---

Run the following command and report the output verbatim, then add a one-line takeaway.

This skill is **one tool call**. If you are driving a multi-step pipeline
(extract → ask → reduce, audits over many sessions, map-reduce synthesis),
do NOT chain invocations of this skill — call the `ask-llm` CLI directly
from a script with stdout redirects. Sub-agents tend to interpret a single
skill's success output as "task done" and bail out of the pipeline.

For large inputs (>50KB total) ask with `--max-words 1500` (or lower) — past
that, the call regularly hits >240s timeouts. For shell-built path lists
prefer `--paths-from <file>` over `--paths $files` (bash word-splitting
hazard).

!ask-llm $ARGUMENTS
