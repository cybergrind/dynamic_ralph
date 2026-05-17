"""`/ask-llm`: bulk file Q&A delegated to a coworker LLM backend.

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

  Paths-from-file mode (recommended for long or shell-built lists — bypasses
  bash word-splitting hazards):
      ask-llm --paths-from <list.txt> --question "<q>"
      ask-llm --paths-from -          --question "<q>"   (paths on stdin)

  Output-sizing knob (recommended for >50KB total input, where unconstrained
  output regularly hits >240s timeouts; cap output target at ~1500 words):
      ask-llm --paths ... --question "..." --max-words 1500

The two flag-mode forms are mutually exclusive: exactly one of `--question`
or `--question-file` is required when any flag is used. If no flag tokens
appear, freeform mode is used. `--paths` and `--paths-from` may be combined.

The backend that actually runs the model is selected by ``--backend <name>``,
the ``COWORKER_BACKEND`` env var, or the default (``opencode``).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from coworker_llm.backend import CoworkerError, CoworkerRequest, get_backend


FLAG_TOKENS = {
    '--paths',
    '--paths-from',
    '--question',
    '-q',
    '--question-file',
    '--max-words',
    '--no-warn',
}


# Calibrated against observed real-world load: 55KB input + 1-word output on
# opencode/haiku took ~31s; 55KB + a 2000-word ask took ~140s. Past ~50KB
# (~12K input tokens) the latency curve gets steep, and audit pipelines have
# repeatedly hit shell-timeout wrappers above this threshold.
LARGE_INPUT_BYTES = 50_000


def _total_input_bytes(paths: tuple[str, ...] | list[str]) -> int:
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            continue
    return total


def _read_paths_from(source: str) -> list[str]:
    if source == '-':
        text = sys.stdin.read()
    else:
        text = Path(source).read_text()
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        out.append(line)
    return out


def parse_freeform(argv: list[str]) -> tuple[list[str], str, str | None]:
    paths: list[str] = []
    words: list[str] = []
    backend: str | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == '--backend':
            if i + 1 >= len(argv):
                raise ValueError('ask-llm: --backend requires a value')
            backend = argv[i + 1]
            i += 2
            continue
        if tok.startswith('@') and len(tok) > 1:
            bare = tok[1:]
            paths.append(bare)
            words.append(bare)
        else:
            words.append(tok)
        i += 1
    return paths, ' '.join(words), backend


def parse_flags(argv: list[str]) -> tuple[list[str], str, str | None, int | None, bool]:
    parser = argparse.ArgumentParser(prog='ask-llm', add_help=False)
    parser.add_argument('--paths', nargs='*', default=[])
    parser.add_argument('--paths-from', default=None)
    parser.add_argument('--backend', default=None)
    parser.add_argument('--max-words', type=int, default=None)
    parser.add_argument('--no-warn', action='store_true', default=False)
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
    paths = list(args.paths)
    if args.paths_from is not None:
        paths.extend(_read_paths_from(args.paths_from))
    return paths, question, args.backend, args.max_words, args.no_warn


def build_prompt(paths: list[str], question: str, max_words: int | None = None) -> str:
    if paths:
        files = ', '.join(paths)
        body = f'Read these files: {files}.\nAnswer concisely: {question}'
    else:
        body = f'Answer concisely: {question}'
    if max_words is not None:
        body += f'\n\nLimit your answer to at most {max_words} words.'
    return body


USAGE = (
    'usage: ask-llm <words> [@path ...]                          (freeform)\n'
    '       ask-llm --paths <p1> <p2>... --question "<q>"        (flags)\n'
    '       ask-llm --paths-from <list>   --question "<q>"       (path list file)\n'
    '       ask-llm --paths <p1>... --question-file <path>       (long Q)\n'
    '       ask-llm ... --max-words <N>                          (output cap)\n'
    '       ask-llm ... --no-warn                                (silence preflight warning)\n'
    'all forms accept --backend <name> (or COWORKER_BACKEND env)\n'
    'observability: every call writes one stderr line:\n'
    '       ask-llm: backend=NAME reads=N in=YKB out=ZKB wall=Ws\n'
    '       use it to calibrate any outer `timeout` wrapper.\n'
    'tip: for long questions containing markdown backticks, $(...) or other\n'
    '     shell-active characters, prefer --question-file <path> to bypass\n'
    '     shell quoting entirely. Use --question-file - to read from stdin.\n'
    'tip: for long or shell-built path lists, prefer --paths-from <file> (one\n'
    '     path per line; blank/`#` lines ignored). Bypasses bash word-splitting\n'
    '     (the $files indirection that occasionally concatenates entries).\n'
    '     Use --paths-from - to read paths from stdin.\n'
    'tip: with >50KB total input, set --max-words 1500 (or lower). Unconstrained\n'
    '     output on large inputs regularly hits multi-minute waits; a preflight\n'
    '     warning fires automatically unless --no-warn is passed.'
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
            paths, question, backend_name, max_words, no_warn = parse_flags(args)
        else:
            paths, question, backend_name = parse_freeform(args)
            max_words = None
            no_warn = False
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    input_bytes = _total_input_bytes(paths)
    if not no_warn and max_words is None and input_bytes > LARGE_INPUT_BYTES:
        kb = input_bytes / 1024
        print(
            f'ask-llm: warning: {kb:.0f}KB total input with no --max-words; '
            f'consider --max-words 1500 to bound latency (>50KB input regularly hits multi-minute waits)',
            file=sys.stderr,
        )

    prompt = build_prompt(paths, question, max_words=max_words)
    request = CoworkerRequest(prompt=prompt, reads=tuple(paths))
    started = time.monotonic()
    try:
        backend = get_backend(backend_name)
        result = backend.run(request)
    except CoworkerError as exc:
        wall = time.monotonic() - started
        print(
            f'ask-llm: backend={backend_name or os.environ.get("COWORKER_BACKEND") or "opencode"} '
            f'reads={len(paths)} in={input_bytes / 1024:.1f}KB out=0.0KB wall={wall:.1f}s status=error',
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    wall = time.monotonic() - started
    out_bytes = len(result.stdout.encode('utf-8', errors='ignore'))
    backend_label = getattr(backend, 'name', None) or backend_name or os.environ.get('COWORKER_BACKEND') or 'opencode'
    print(
        f'ask-llm: backend={backend_label} reads={len(paths)} '
        f'in={input_bytes / 1024:.1f}KB out={out_bytes / 1024:.1f}KB wall={wall:.1f}s',
        file=sys.stderr,
    )
    print(result.stdout)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
