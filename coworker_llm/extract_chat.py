"""`/extract-chat`: process Claude Code session transcripts via opencode.

Two modes:

  Summarize (default): print opencode's summary/answer to stdout.
      extract-chat session.jsonl
      extract-chat @session.jsonl list decisions

  Extract-to-file: with `-o <path>` (or `--output <path>`), opencode writes
  the extracted plain-text transcript (or answer to --question) to that file.
      extract-chat session.jsonl -o /tmp/chat.txt
      extract-chat session.jsonl -o /tmp/x.txt --question "List decisions"

Freeform tokens (anything not consumed by a flag) become the question; if
none are given in extract-mode, opencode is asked to write a plain-text
rendering of the conversation.
"""

from __future__ import annotations

import sys

from coworker_llm.opencode import OpenCodeError, run_opencode


DEFAULT_QUESTION = 'a concise summary of decisions and changes'
DEFAULT_EXTRACT_INSTRUCTION = 'a faithful plain-text rendering of the conversation'


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


def build_prompt(transcript: str, output: str | None, question: str | None) -> str:
    if output is not None and question:
        return (
            f'Read the Claude Code session transcript at {transcript}. '
            f'Answer the question: {question}. '
            f'Write your answer to the file {output}.'
        )
    if output is not None:
        return (
            f'Read the Claude Code session transcript at {transcript} '
            f'and write {DEFAULT_EXTRACT_INSTRUCTION} to the file {output}.'
        )
    q = question if question else DEFAULT_QUESTION
    return f'Read this Claude Code session transcript at {transcript} and produce {q}.'


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        print(
            'usage: extract-chat <@path|path> [question words...] [-o <output>]',
            file=sys.stderr,
        )
        return 2
    try:
        transcript, output, question = parse_argv(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    prompt = build_prompt(transcript, output, question)
    try:
        reply = run_opencode(prompt)
    except OpenCodeError as exc:
        print(str(exc), file=sys.stderr)
        return exc.returncode or 1
    print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
