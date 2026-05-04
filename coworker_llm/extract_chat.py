"""`/extract-chat`: summarize a Claude Code session transcript via opencode.

Free-form argv: the first `@path` (or first positional token) is the transcript
path; remaining words form the question. With no extra words, a default summary
question is used.
"""

from __future__ import annotations

import sys

from coworker_llm.opencode import OpenCodeError, run_opencode


DEFAULT_QUESTION = 'a concise summary of decisions and changes'


def parse_freeform(argv: list[str]) -> tuple[str, str | None]:
    transcript: str | None = None
    words: list[str] = []
    for tok in argv:
        bare = tok[1:] if tok.startswith('@') and len(tok) > 1 else tok
        if transcript is None and (tok.startswith('@') or '/' in tok or '.' in tok):
            transcript = bare
        else:
            words.append(bare)
    if transcript is None:
        raise ValueError('extract-chat: missing transcript path (use @path or a path-like token)')
    question = ' '.join(words) if words else None
    return transcript, question


def build_prompt(transcript: str, question: str | None) -> str:
    q = question if question else DEFAULT_QUESTION
    return f'Read this Claude Code session transcript at {transcript} and produce {q}.'


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print('usage: extract-chat <@path> [question words...]', file=sys.stderr)
        return 2
    try:
        transcript, question = parse_freeform(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    prompt = build_prompt(transcript, question)
    try:
        reply = run_opencode(prompt)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
