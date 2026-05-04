"""`/extract-chat`: process Claude Code session transcripts.

Default mode parses the JSONL **locally** and emits plain-text role-prefixed
content. No opencode call, no model context limit — works on multi-megabyte
transcripts.

Modes:

  Local (default): walk the JSONL, extract `user`/`assistant` messages, write
  to stdout or `-o <path>`.
      extract-chat session.jsonl
      extract-chat session.jsonl -o /tmp/chat.txt

  Question (delegates to opencode): with `--question`, opencode is asked
  about the transcript. Subject to the model's context limit; large
  transcripts will fail.
      extract-chat session.jsonl --question "List all decisions"
      extract-chat session.jsonl -o /tmp/answer.txt --question "..."

Tokens prefixed with `@` are accepted as paths in either mode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from coworker_llm.opencode import OpenCodeError, run_opencode


DEFAULT_QUESTION = 'a concise summary of decisions and changes'


def _strip_at(tok: str) -> str:
    return tok[1:] if tok.startswith('@') and len(tok) > 1 else tok


def parse_argv(argv: list[str]) -> tuple[str, str | None, str | None]:
    """Return (transcript, output_path, question). Raises ValueError on missing transcript."""
    transcript: str | None = None
    output: str | None = None
    question_words: list[str] = []
    explicit_question: str | None = None

    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ('-o', '--output'):
            if i + 1 >= len(argv):
                raise ValueError(f'extract-chat: {tok} requires a path argument')
            output = argv[i + 1]
            i += 2
            continue
        if tok in ('-q', '--question'):
            if i + 1 >= len(argv):
                raise ValueError(f'extract-chat: {tok} requires a value')
            explicit_question = argv[i + 1]
            i += 2
            continue
        bare = _strip_at(tok)
        if transcript is None and (tok.startswith('@') or '/' in tok or '.' in tok):
            transcript = bare
        else:
            question_words.append(bare)
        i += 1

    if transcript is None:
        raise ValueError('extract-chat: missing transcript path (use @path or a path-like token)')

    question: str | None = explicit_question
    if question is None and question_words:
        question = ' '.join(question_words)
    return transcript, output, question


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get('type')
            if btype == 'text':
                parts.append(str(block.get('text', '')))
            elif btype == 'tool_use':
                name = block.get('name', '?')
                cmd = block.get('input', {})
                parts.append(f'[tool_use: {name} {json.dumps(cmd, default=str)[:200]}]')
            elif btype == 'tool_result':
                inner = block.get('content', '')
                if isinstance(inner, list):
                    inner = '\n'.join(
                        b.get('text', '') if isinstance(b, dict) and b.get('type') == 'text' else '' for b in inner
                    )
                parts.append(f'[tool_result: {str(inner)[:300]}]')
            # 'thinking' blocks are intentionally skipped
        return '\n'.join(p for p in parts if p)
    return ''


def extract_transcript(jsonl_path: str) -> str:
    """Parse a Claude Code session JSONL into role-prefixed plain text."""
    out: list[str] = []
    with open(jsonl_path, encoding='utf-8') as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            t = obj.get('type')
            if t not in ('user', 'assistant'):
                continue
            msg = obj.get('message')
            if not isinstance(msg, dict):
                continue
            role = str(msg.get('role', t)).upper()
            text = _content_to_text(msg.get('content'))
            if not text.strip():
                continue
            out.append(f'{role}: {text}')
    return '\n\n'.join(out)


def build_prompt(transcript: str, output: str | None, question: str | None) -> str:
    """Used only by --question mode: ask opencode about the transcript."""
    if output is not None and question:
        return (
            f'Read the Claude Code session transcript at {transcript}. '
            f'Answer the question: {question}. '
            f'Write your answer to the file {output}.'
        )
    if output is not None:
        return (
            f'Read the Claude Code session transcript at {transcript} '
            f'and write a faithful plain-text rendering of the conversation to the file {output}.'
        )
    q = question if question else DEFAULT_QUESTION
    return f'Read this Claude Code session transcript at {transcript} and produce {q}.'


USAGE = 'usage: extract-chat <@path|path> [question words...] [-o <output>] [--question "<q>"]'


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print(USAGE, file=sys.stderr)
        return 2
    if args[0] in ('-h', '--help'):
        print(USAGE)
        return 0
    try:
        transcript, output, question = parse_argv(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # Local mode: no question → parse JSONL ourselves.
    if question is None:
        try:
            text = extract_transcript(transcript)
        except OSError as exc:
            print(f'extract-chat: cannot read {transcript}: {exc}', file=sys.stderr)
            return 1
        if output is not None:
            out_path = Path(output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding='utf-8')
            print(f'extract-chat: wrote {out_path.stat().st_size} bytes to {output}')
        else:
            print(text)
        return 0

    # Question mode: route through opencode.
    prompt = build_prompt(transcript, output, question)
    work_dir: str | None = None
    attach: tuple[str, ...] = ()
    if output is not None:
        out_path = Path(output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = str(out_path.parent)
        attach = (str(Path(transcript).resolve()),)
    try:
        reply = run_opencode(prompt, dir=work_dir, attach=attach)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    if reply.strip():
        print(reply)
    if output is not None:
        out_path = Path(output)
        if out_path.is_file():
            print(f'extract-chat: wrote {out_path.stat().st_size} bytes to {output}')
            return 0
        print(f'extract-chat: opencode did not create the output file {output}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
