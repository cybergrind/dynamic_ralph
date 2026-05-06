"""`/llm-write`: generate boilerplate via a coworker LLM backend using a reference for style.

Three invocation styles, all supported:

  Flag mode (short specs):
      llm-write --spec "<what>" --context <reference> --target <output>

  Spec-file mode (recommended for long specs — bypasses shell quoting hazards
  like backticks and $() that LLMs reflexively use in natural-language specs):
      llm-write --spec-file <path> --context <reference> --target <output>
      llm-write --spec-file -      --context <reference> --target <output>

  Freeform: first @path is the context (style reference), second @path is the
  target output path; remaining words form the spec.
      llm-write make a pytest for @ref.py into @out.py

The backend that actually runs the model is selected by ``--backend <name>``,
the ``COWORKER_BACKEND`` env var, or the default (``opencode``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coworker_llm.backend import CoworkerError, CoworkerRequest, get_backend


FLAG_TOKENS = {'--spec', '--spec-file', '--context', '--target'}


def parse_freeform(argv: list[str]) -> tuple[str, str, str, str | None]:
    paths: list[str] = []
    words: list[str] = []
    backend: str | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == '--backend':
            if i + 1 >= len(argv):
                raise ValueError('llm-write: --backend requires a value')
            backend = argv[i + 1]
            i += 2
            continue
        if tok.startswith('@') and len(tok) > 1:
            paths.append(tok[1:])
        else:
            words.append(tok)
        i += 1
    if len(paths) < 2:
        raise ValueError(
            'llm-write freeform requires two @paths (first=context, second=target). '
            'Or use --spec/--context/--target flags.',
        )
    context, target = paths[0], paths[1]
    spec = ' '.join(words).strip()
    if not spec:
        raise ValueError('llm-write freeform requires spec words alongside the @paths.')
    return spec, context, target, backend


def parse_flags(argv: list[str]) -> tuple[str, str, str, str | None]:
    parser = argparse.ArgumentParser(prog='llm-write', add_help=False)
    spec_group = parser.add_mutually_exclusive_group(required=True)
    spec_group.add_argument('--spec')
    spec_group.add_argument('--spec-file')
    parser.add_argument('--context', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--backend', default=None)
    args = parser.parse_args(argv)
    if args.spec is not None:
        spec = args.spec
    elif args.spec_file == '-':
        spec = sys.stdin.read()
    else:
        spec = Path(args.spec_file).read_text()
    return spec, args.context, args.target, args.backend


def build_prompt(spec: str, context: str, target: str) -> str:
    return f'Using {context} as a style reference, write a new file at {target}.\nSpecification: {spec}'


USAGE = (
    'usage: llm-write --spec "<what>" --context <ref> --target <out>\n'
    '       llm-write --spec-file <path> --context <ref> --target <out>\n'
    '       llm-write <spec words> @<context> @<target>\n'
    'all forms accept --backend <name> (or COWORKER_BACKEND env)\n'
    'tip: for long specs containing markdown backticks, $(...) or other\n'
    '     shell-active characters, prefer --spec-file <path> to bypass shell\n'
    '     quoting entirely. Use --spec-file - to read from stdin.'
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
        if use_flags:
            spec, context, target, backend_name = parse_flags(args)
        else:
            spec, context, target, backend_name = parse_freeform(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    prompt = build_prompt(spec, context, target)
    target_path = Path(target).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir = str(target_path.parent)
    context_abs = str(Path(context).resolve())
    request = CoworkerRequest(
        prompt=prompt,
        reads=(context_abs,),
        writes_dir=work_dir,
        expected_target=str(target_path),
    )
    try:
        backend = get_backend(backend_name)
        result = backend.run(request)
    except CoworkerError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(result.stdout)
    if target_path.is_file():
        print(f'llm-write: wrote {target_path.stat().st_size} bytes to {target}')
        return 0
    print(f'llm-write: coworker did not create the target file {target}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
