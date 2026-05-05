"""`/ask-llm`: bulk file Q&A delegated to opencode.

Three invocation styles, all supported:

  Freeform (Claude Code style): tokens prefixed with `@` are file paths
  (the prefix is stripped); everything else forms the question.
      ask-llm what entrypoints @pyproject.toml has

  Flag mode (short questions):
      ask-llm --paths a.py b.py --question "What IPs are used?"

  Question-file mode (recommended for long questions — bypasses shell quoting
  hazards like backticks and $() that LLMs reflexively use in natural-language
  questions):
      ask-llm --paths a.py b.py --question-file <path>
      ask-llm --paths a.py b.py --question-file -    (read from stdin)

The two flag-mode forms are mutually exclusive: exactly one of `--question`
or `--question-file` is required when any flag is used. If no flag tokens
appear, freeform mode is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coworker_llm.opencode import OpenCodeError, run_opencode


FLAG_TOKENS = {'--paths', '--question', '-q', '--question-file'}


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
    question_group = parser.add_mutually_exclusive_group(required=True)
    question_group.add_argument('--question', '-q')
    question_group.add_argument('--question-file')
    args = parser.parse_args(argv)
    if args.question is not None:
        question = args.question
    elif args.question_file == '-':
        question = sys.stdin.read()
    else:
        question = Path(args.question_file).read_text()
    return list(args.paths), question


def build_prompt(paths: list[str], question: str) -> str:
    if paths:
        files = ', '.join(paths)
        return f'Read these files: {files}.\nAnswer concisely: {question}'
    return f'Answer concisely: {question}'


USAGE = (
    'usage: ask-llm <words> [@path ...]                          (freeform)\n'
    '       ask-llm --paths <p1> <p2>... --question "<q>"        (flags)\n'
    '       ask-llm --paths <p1> <p2>... --question-file <path>  (long Q)\n'
    'tip: for long questions containing markdown backticks, $(...) or other\n'
    '     shell-active characters, prefer --question-file <path> to bypass\n'
    '     shell quoting entirely. Use --question-file - to read from stdin.'
)


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    if args[0] in ('-h', '--help'):
        print(USAGE)
        return 0

    use_flags = any(tok in FLAG_TOKENS for tok in args)
    try:
        paths, question = parse_flags(args) if use_flags else parse_freeform(args)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    prompt = build_prompt(paths, question)
    attach = tuple(paths)
    try:
        reply = run_opencode(prompt, attach=attach)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
