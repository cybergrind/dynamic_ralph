"""`/llm-write`: generate boilerplate via opencode using a reference file."""

from __future__ import annotations

import argparse
import sys

from coworker_llm.opencode import OpenCodeError, run_opencode


def build_prompt(spec: str, reference: str, output: str) -> str:
    return f'Using {reference} as a style reference, write a new file at {output}.\nSpecification: {spec}'


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='llm-write', description='Generate boilerplate via opencode.')
    parser.add_argument('--spec', required=True, help='What to generate.')
    parser.add_argument('--reference', required=True, help='Reference file for style.')
    parser.add_argument('--output', required=True, help='Target output path.')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    prompt = build_prompt(args.spec, args.reference, args.output)
    try:
        reply = run_opencode(prompt)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
