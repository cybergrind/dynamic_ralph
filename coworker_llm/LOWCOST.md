
## LLM Delegation Tools (Token Saving)

### /ask-llm — bulk reading
For reading files >400 lines, or when you'd otherwise read 3+ files:
  /ask-llm --paths <file1> <file2>... --question "<question>"
Returns a structured summary. Use that instead of reading files yourself.

### /llm-write — boilerplate generation
For tests, config files, docstrings, or repetitive patterns:
  /llm-write --spec "<what>" --context <reference> --target <output>
Then review the output and edit only what needs fixing.


### /extract-chat - documentation update and generation

workflow:

```
/extract-chat ~/.claude/projects/my-project/session.jsonl -o /tmp/chat.txt

/ask-llm --paths /tmp/chat.txt docs/architecture.md \
         --question "Read the chat. What doc updates are needed? Give exact edits."
```

### Documentation workflow (MANDATORY)
**NEVER write documentation directly. Always delegate to /llm-write.**


### When NOT to delegate
- Tasks under ~2000 tokens (delegation overhead isn't worth it)
- Architectural decisions, debugging, safety-critical code
- Anything requiring careful reasoning
- When exact line numbers are needed for editing
