"""`/ask-llm`: bulk file Q&A delegated to opencode.

Accepts free-form argv: tokens prefixed with `@` are treated as file paths
(matching Claude Code's @file mention syntax); everything else forms the
question. The `@` prefix is stripped from both the path list and the question.
"""

from __future__ import annotations

import sys

from coworker_llm.opencode import OpenCodeError, run_opencode


def parse_freeform(argv: list[str]) -> tuple[list[str], str]:
    paths: list[str] = []
    words: list[str] = []
    for tok in argv:
        if tok.startswith('@') and len(tok) > 1:
            bare = tok[1:]
            paths.append(bare)
            words.append(bare)
        else:
            words.append(tok)
    return paths, ' '.join(words)


def build_prompt(paths: list[str], question: str) -> str:
    if paths:
        files = ', '.join(paths)
        return f'Read these files: {files}.\nAnswer concisely: {question}'
    return f'Answer concisely: {question}'


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print('usage: ask-llm <words> [@path ...] — free-form, @ marks file paths', file=sys.stderr)
        return 2
    paths, question = parse_freeform(args)
    prompt = build_prompt(paths, question)
    try:
        reply = run_opencode(prompt)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
