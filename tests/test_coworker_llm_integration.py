"""End-to-end test that hits real opencode. Opt-in via RUN_INTEGRATION=1."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from coworker_llm import ask_llm


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get('RUN_INTEGRATION') != '1', reason='set RUN_INTEGRATION=1 to run'),
    pytest.mark.skipif(shutil.which('opencode') is None, reason='opencode CLI not installed'),
]


def test_ask_llm_finds_token_in_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sentinel = 'BANANA-7421'
    fixture = tmp_path / 'fixture.txt'
    fixture.write_text(f'project notes:\nthe deploy key is {sentinel}\n')

    rc = ask_llm.main([str(fixture), '--question', 'What unusual token appears in this file?'])

    assert rc == 0
    out = capsys.readouterr().out
    assert sentinel in out, f'expected opencode reply to mention {sentinel}; got:\n{out}'
