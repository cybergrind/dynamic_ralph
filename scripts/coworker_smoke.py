#!/usr/bin/env python3
"""Cross-backend smoke harness for coworker_llm.

Runs one task through one or more backends and prints what came back.
Built-in canonical tasks set up tmpdir fixtures with random sentinel
tokens and verify the agent's output / written files contain them.

Usage:
    uv run scripts/coworker_smoke.py --backend opencode
    uv run scripts/coworker_smoke.py --backend opencode --backend claude-code
    uv run scripts/coworker_smoke.py --backend opencode --task ask
    uv run scripts/coworker_smoke.py --backend opencode --prompt "list 3 fruits"
    uv run scripts/coworker_smoke.py --backend opencode --dry-run

Tasks (built-in):
    ask       — read a fixture, find a sentinel token in its content
    write     — given a context fixture, write a sentinel into a target file
    roundtrip — read + write + summarize (default; exercises every field)
"""

from __future__ import annotations

import argparse
import random
import string
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from coworker_llm.backend import (
    CoworkerError,
    CoworkerRequest,
    CoworkerResult,
    get_backend,
    list_backends,
)


@dataclass
class TaskFixture:
    name: str
    request: CoworkerRequest
    workdir: Path
    sentinels: dict[str, str]
    target: Path | None
    check_stdout: bool


def _sentinel(prefix: str) -> str:
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f'{prefix}-{suffix}'


def build_ask_task(workdir: Path) -> TaskFixture:
    sentinel = _sentinel('BANANA')
    fixture = workdir / 'fixture.txt'
    fixture.write_text(f'project notes:\nthe deploy key is {sentinel}\n')
    prompt = (
        f'Read the file at {fixture}. '
        'There is exactly one all-caps token of the form WORD-XXXXXX in it. '
        'Reply with that token and nothing else.'
    )
    return TaskFixture(
        name='ask',
        request=CoworkerRequest(prompt=prompt, reads=(str(fixture),)),
        workdir=workdir,
        sentinels={'stdout': sentinel},
        target=None,
        check_stdout=True,
    )


def build_write_task(workdir: Path) -> TaskFixture:
    sentinel = _sentinel('PEAR')
    reference = workdir / 'reference.txt'
    reference.write_text(f'this reference contains the token {sentinel}\n')
    target = workdir / 'generated.txt'
    prompt = (
        f'Read {reference} which contains a token of the form WORD-XXXXXX. '
        f'Write a new file at {target} containing exactly that token on a single line. '
        'No other content.'
    )
    return TaskFixture(
        name='write',
        request=CoworkerRequest(
            prompt=prompt,
            reads=(str(reference),),
            writes_dir=str(workdir),
            expected_target=str(target),
        ),
        workdir=workdir,
        sentinels={'target': sentinel},
        target=target,
        check_stdout=False,
    )


def build_roundtrip_task(workdir: Path) -> TaskFixture:
    read_sent = _sentinel('KIWI')
    fixture = workdir / 'fixture.txt'
    fixture.write_text(f'inventory line: token {read_sent} is the active code\n')
    target = workdir / 'summary.txt'
    prompt = (
        f'Read {fixture}. It contains exactly one token of the form WORD-XXXXXX. '
        f'Write that exact token to a new file at {target} on a single line. '
        'Then in your reply state the token verbatim.'
    )
    return TaskFixture(
        name='roundtrip',
        request=CoworkerRequest(
            prompt=prompt,
            reads=(str(fixture),),
            writes_dir=str(workdir),
            expected_target=str(target),
        ),
        workdir=workdir,
        sentinels={'stdout': read_sent, 'target': read_sent},
        target=target,
        check_stdout=True,
    )


def build_custom_task(
    workdir: Path,
    prompt: str,
    reads: list[str],
    writes_dir: str | None,
    target: str | None,
) -> TaskFixture:
    return TaskFixture(
        name='custom',
        request=CoworkerRequest(
            prompt=prompt,
            reads=tuple(reads),
            writes_dir=writes_dir,
            expected_target=target,
        ),
        workdir=workdir,
        sentinels={},
        target=Path(target) if target else None,
        check_stdout=False,
    )


@dataclass
class RunOutcome:
    backend: str
    task: str
    returncode: int | None
    elapsed: float
    error: str | None
    target_exists: bool | None
    target_size: int | None
    sentinel_in_stdout: bool | None
    sentinel_in_target: bool | None
    stdout: str


def run_one(backend_name: str, fixture: TaskFixture, dry_run: bool) -> RunOutcome:
    print(f'=== backend={backend_name} task={fixture.name} ===')
    print('request:')
    print(f'  prompt: {_truncate(fixture.request.prompt, 160)}')
    print(f'  reads: {list(fixture.request.reads)}')
    print(f'  writes_dir: {fixture.request.writes_dir}')
    print(f'  expected_target: {fixture.request.expected_target}')

    try:
        backend = get_backend(backend_name)
    except CoworkerError as exc:
        print(f'  ! cannot load backend: {exc}')
        return RunOutcome(
            backend=backend_name, task=fixture.name, returncode=None,
            elapsed=0.0, error=str(exc), target_exists=None, target_size=None,
            sentinel_in_stdout=None, sentinel_in_target=None, stdout='',
        )

    describe = getattr(backend, 'describe', None)
    if callable(describe):
        try:
            argv = describe(fixture.request)
            print(f'argv: {argv}')
        except Exception as exc:  # noqa: BLE001
            print(f'argv: <describe() failed: {exc}>')

    if not backend.is_available():
        print('  ! backend reports is_available()=False')

    if dry_run:
        print('  (dry run; not invoking)')
        print()
        return RunOutcome(
            backend=backend_name, task=fixture.name, returncode=None,
            elapsed=0.0, error=None, target_exists=None, target_size=None,
            sentinel_in_stdout=None, sentinel_in_target=None, stdout='',
        )

    started = time.perf_counter()
    error: str | None = None
    result: CoworkerResult | None = None
    rc: int | None = 0
    try:
        result = backend.run(fixture.request)
    except CoworkerError as exc:
        error = str(exc)
        rc = exc.returncode
    elapsed = time.perf_counter() - started

    print(f'elapsed: {elapsed:.2f}s')
    print(f'returncode: {rc if error else 0}')
    if error:
        print(f'error: {error}')

    target_exists: bool | None = None
    target_size: int | None = None
    sent_in_target: bool | None = None
    if fixture.target is not None:
        target_exists = fixture.target.is_file()
        if target_exists:
            text = fixture.target.read_text(errors='replace')
            target_size = len(text.encode('utf-8'))
            print(f'target file: exists ({target_size} bytes)')
            if 'target' in fixture.sentinels:
                sent_in_target = fixture.sentinels['target'] in text
                print(f'sentinel in target: {"yes" if sent_in_target else "no"}')
        else:
            print(f'target file: missing ({fixture.target})')
            sent_in_target = False if 'target' in fixture.sentinels else None

    sent_in_stdout: bool | None = None
    stdout_text = result.stdout if result else ''
    if fixture.check_stdout and 'stdout' in fixture.sentinels:
        sent_in_stdout = fixture.sentinels['stdout'] in stdout_text
        print(f'sentinel in stdout: {"yes" if sent_in_stdout else "no"}')

    if stdout_text:
        print(f'--- stdout ({len(stdout_text)} chars) ---')
        print(stdout_text)
        if not stdout_text.endswith('\n'):
            print()
        print('--- end ---')
    else:
        print('--- stdout: empty ---')
    print()

    return RunOutcome(
        backend=backend_name,
        task=fixture.name,
        returncode=rc,
        elapsed=elapsed,
        error=error,
        target_exists=target_exists,
        target_size=target_size,
        sentinel_in_stdout=sent_in_stdout,
        sentinel_in_target=sent_in_target,
        stdout=stdout_text,
    )


def _truncate(s: str, n: int) -> str:
    s = s.replace('\n', ' ')
    return s if len(s) <= n else s[: n - 3] + '...'


def print_summary(outcomes: list[RunOutcome]) -> None:
    print('=' * 72)
    print('Summary')
    print('-' * 72)
    print(f'{"backend":<14}{"task":<12}{"rc":>4}  {"elapsed":>8}  {"target":<8}{"sent_out":<10}{"sent_tgt":<10}')
    for o in outcomes:
        rc = '-' if o.returncode is None else str(o.returncode)
        target = '-' if o.target_exists is None else ('yes' if o.target_exists else 'no')
        sent_out = (
            '-' if o.sentinel_in_stdout is None
            else ('yes' if o.sentinel_in_stdout else 'no')
        )
        sent_tgt = (
            '-' if o.sentinel_in_target is None
            else ('yes' if o.sentinel_in_target else 'no')
        )
        print(f'{o.backend:<14}{o.task:<12}{rc:>4}  {o.elapsed:>7.2f}s  {target:<8}{sent_out:<10}{sent_tgt:<10}')


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='coworker_smoke',
        description=__doc__.split('\n', 1)[0] if __doc__ else None,
    )
    parser.add_argument(
        '--backend', '-b',
        action='append',
        default=[],
        help='backend name (repeatable; required unless --list-backends)',
    )
    parser.add_argument(
        '--task', '-t',
        choices=['ask', 'write', 'roundtrip'],
        default='roundtrip',
        help='built-in canonical task (default: roundtrip)',
    )
    parser.add_argument('--prompt', help='ad-hoc prompt; overrides --task')
    parser.add_argument('--prompt-file', help='read prompt from file; overrides --task')
    parser.add_argument('--read', action='append', default=[], help='ad-hoc read path (repeatable)')
    parser.add_argument('--writes-dir', help='ad-hoc writes_dir')
    parser.add_argument('--target', help='ad-hoc expected_target (informational)')
    parser.add_argument('--dry-run', action='store_true', help='print request and exit; do not invoke')
    parser.add_argument('--list-backends', action='store_true', help='list known backends and exit')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv) if argv is not None else sys.argv[1:])

    if args.list_backends:
        for name in list_backends():
            try:
                backend = get_backend(name)
                avail = 'available' if backend.is_available() else 'not installed'
            except CoworkerError as exc:
                avail = f'error: {exc}'
            print(f'{name}\t{avail}')
        return 0

    if not args.backend:
        print('error: --backend is required (use --list-backends to see options)', file=sys.stderr)
        return 2

    custom = args.prompt is not None or args.prompt_file is not None
    if custom and args.prompt and args.prompt_file:
        print('error: --prompt and --prompt-file are mutually exclusive', file=sys.stderr)
        return 2

    outcomes: list[RunOutcome] = []
    with tempfile.TemporaryDirectory(prefix='coworker_smoke_') as raw_workdir:
        workdir = Path(raw_workdir)
        for backend_name in args.backend:
            per_run_dir = workdir / f'{backend_name}_{args.task}'
            per_run_dir.mkdir(parents=True, exist_ok=True)
            if custom:
                prompt = args.prompt
                if prompt is None:
                    prompt = (
                        sys.stdin.read() if args.prompt_file == '-'
                        else Path(args.prompt_file).read_text()
                    )
                fixture = build_custom_task(
                    per_run_dir,
                    prompt=prompt,
                    reads=args.read,
                    writes_dir=args.writes_dir,
                    target=args.target,
                )
            elif args.task == 'ask':
                fixture = build_ask_task(per_run_dir)
            elif args.task == 'write':
                fixture = build_write_task(per_run_dir)
            else:
                fixture = build_roundtrip_task(per_run_dir)
            outcomes.append(run_one(backend_name, fixture, args.dry_run))

    if len(outcomes) > 1 or not args.dry_run:
        print_summary(outcomes)

    failed = sum(1 for o in outcomes if o.error is not None or (o.returncode not in (0, None)))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
