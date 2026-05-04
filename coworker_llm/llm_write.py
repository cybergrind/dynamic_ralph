"""`/llm-write`: generate boilerplate via opencode using a reference for style.

Two invocation styles, both supported:

  Flag mode (matches LOWCOST.md):
      llm-write --spec "<what>" --context <reference> --target <output>

  Freeform: first @path is the context (style reference), second @path is the
  target output path; remaining words form the spec.
      llm-write make a pytest for @ref.py into @out.py
"""

from __future__ import annotations

import argparse
import sys

from coworker_llm.opencode import OpenCodeError, run_opencode


def parse_freeform(argv: list[str]) -> tuple[str, str, str]:
    paths: list[str] = []
    words: list[str] = []
    for tok in argv:
        if tok.startswith('@') and len(tok) > 1:
            paths.append(tok[1:])
        else:
            words.append(tok)
    if len(paths) < 2:
        raise ValueError(
            'llm-write freeform requires two @paths (first=context, second=target). '
            'Or use --spec/--context/--target flags.',
        )
    context, target = paths[0], paths[1]
    spec = ' '.join(words).strip()
    if not spec:
        raise ValueError('llm-write freeform requires spec words alongside the @paths.')
    return spec, context, target


def parse_flags(argv: list[str]) -> tuple[str, str, str]:
    parser = argparse.ArgumentParser(prog='llm-write', add_help=False)
    parser.add_argument('--spec', required=True)
    parser.add_argument('--context', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args(argv)
    return args.spec, args.context, args.target


def build_prompt(spec: str, context: str, target: str) -> str:
    return f'Using {context} as a style reference, write a new file at {target}.\nSpecification: {spec}'


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print(
            'usage: llm-write --spec "<what>" --context <ref> --target <out>\n'
            '       llm-write <spec words> @<context> @<target>',
            file=sys.stderr,
        )
        return 2

    use_flags = any(tok in {'--spec', '--context', '--target'} for tok in args)
    try:
        spec, context, target = parse_flags(args) if use_flags else parse_freeform(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    prompt = build_prompt(spec, context, target)
    try:
        reply = run_opencode(prompt)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
