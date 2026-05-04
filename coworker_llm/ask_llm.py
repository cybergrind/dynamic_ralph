"""`/ask-llm`: bulk file Q&A delegated to opencode.

Two invocation styles, both supported:

  Freeform (Claude Code style): tokens prefixed with `@` are file paths
  (the prefix is stripped); everything else forms the question.
      ask-llm what entrypoints @pyproject.toml has

  Flag mode (article style):
      ask-llm --paths a.py b.py --question "What IPs are used?"

The two are mutually exclusive: if `--paths` or `--question` appears in argv,
flag mode is used; otherwise freeform.
"""

from __future__ import annotations

import argparse
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


def parse_flags(argv: list[str]) -> tuple[list[str], str]:
    parser = argparse.ArgumentParser(prog='ask-llm', add_help=False)
    parser.add_argument('--paths', nargs='*', default=[])
    parser.add_argument('--question', '-q', required=True)
    args = parser.parse_args(argv)
    return list(args.paths), args.question


def build_prompt(paths: list[str], question: str) -> str:
    if paths:
        files = ', '.join(paths)
        return f'Read these files: {files}.\nAnswer concisely: {question}'
    return f'Answer concisely: {question}'


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print(
            'usage: ask-llm <words> [@path ...]               (freeform)\n'
            '       ask-llm --paths <p1> <p2>... --question "<q>"  (flags)',
            file=sys.stderr,
        )
        return 2

    use_flags = any(tok in {'--paths', '--question', '-q'} for tok in args)
    paths, question = parse_flags(args) if use_flags else parse_freeform(args)

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
