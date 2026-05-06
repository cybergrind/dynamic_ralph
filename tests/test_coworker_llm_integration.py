"""End-to-end tests that hit a real coworker LLM backend.

Opt-in: set RUN_INTEGRATION=1. Each test is slow (typically ~30-90s) because
the backend invokes a hosted model. Tests run once per backend whose CLI is
installed on PATH.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from coworker_llm import ask_llm, extract_chat, llm_write
from coworker_llm.backend import get_backend, list_backends


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get('RUN_INTEGRATION') != '1', reason='set RUN_INTEGRATION=1 to run'),
]


def _available_backends() -> list[str]:
    out: list[str] = []
    for name in list_backends():
        try:
            if get_backend(name).is_available():
                out.append(name)
        except Exception:  # noqa: BLE001
            continue
    return out


@pytest.fixture(params=_available_backends())
def backend_name(request: pytest.FixtureRequest) -> str:
    if not request.param:
        pytest.skip('no coworker backend installed')
    return request.param


def test_ask_llm_finds_token_in_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend_name: str,
) -> None:
    sentinel = 'BANANA-7421'
    fixture = tmp_path / 'fixture.txt'
    fixture.write_text(f'project notes:\nthe deploy key is {sentinel}\n')

    rc = ask_llm.main([
        '--backend', backend_name,
        '--paths', str(fixture),
        '--question', 'What unusual all-caps token appears in this file?',
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert sentinel in out, f'[{backend_name}] expected reply to mention {sentinel}; got:\n{out}'


def test_llm_write_produces_target_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend_name: str,
) -> None:
    sentinel = 'PEAR-3030'
    reference = tmp_path / 'reference.txt'
    reference.write_text(f'this reference contains the token {sentinel}\n')
    target = tmp_path / 'generated.txt'

    rc = llm_write.main([
        '--backend', backend_name,
        '--spec', f'Write a single line file containing exactly the token {sentinel}.',
        '--context', str(reference),
        '--target', str(target),
    ])

    assert rc == 0, f'[{backend_name}] llm-write failed; stderr/stdout:\n{capsys.readouterr()}'
    assert target.is_file(), f'[{backend_name}] expected coworker to create the target file'
    assert sentinel in target.read_text()


def test_extract_chat_question_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], backend_name: str,
) -> None:
    sentinel = 'KIWI-9090'
    transcript = tmp_path / 'session.jsonl'
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hi"}}\n'
        f'{{"type":"assistant","message":{{"role":"assistant","content":"the secret token is {sentinel}"}}}}\n',
    )
    output = tmp_path / 'chat.txt'

    rc = extract_chat.main([
        str(transcript),
        '--backend', backend_name,
        '-o', str(output),
        '--question', 'What is the exact secret token mentioned in the transcript? Reply with that token.',
    ])

    assert rc == 0, f'[{backend_name}] extract-chat failed; stderr/stdout:\n{capsys.readouterr()}'
    assert output.is_file(), f'[{backend_name}] expected coworker to create the output file'
    assert sentinel in output.read_text()
