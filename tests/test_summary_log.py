"""Tests for append_summary() and _print_progress() summary.log integration."""

import importlib.util
import re
from pathlib import Path

import pytest


# Import from the bin script
_bin_path = Path(__file__).resolve().parent.parent / 'bin' / 'run_dynamic_ralph.py'
_spec = importlib.util.spec_from_file_location('run_dynamic_ralph', _bin_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

append_summary = _mod.append_summary
_print_progress = _mod._print_progress

TIMESTAMP_RE = re.compile(r'^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] .+\n$')


class TestAppendSummary:
    def test_creates_file(self, tmp_path: Path) -> None:
        append_summary('hello world', tmp_path)
        log = tmp_path / 'summary.log'
        assert log.exists()
        lines = log.read_text().splitlines(keepends=True)
        assert len(lines) == 1
        assert TIMESTAMP_RE.match(lines[0])
        assert 'hello world' in lines[0]

    def test_appends_multiple(self, tmp_path: Path) -> None:
        append_summary('first', tmp_path)
        append_summary('second', tmp_path)
        lines = (tmp_path / 'summary.log').read_text().splitlines(keepends=True)
        assert len(lines) == 2
        assert 'first' in lines[0]
        assert 'second' in lines[1]

    def test_newline_handling(self, tmp_path: Path) -> None:
        append_summary('line1\nline2\nline3', tmp_path)
        lines = (tmp_path / 'summary.log').read_text().splitlines(keepends=True)
        assert len(lines) == 1
        assert 'line1 line2 line3' in lines[0]


class TestPrintProgressSummary:
    def test_with_shared_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _print_progress('test message', shared_dir=tmp_path)
        log = tmp_path / 'summary.log'
        assert log.exists()
        content = log.read_text()
        assert 'test message' in content
        assert capsys.readouterr().out.strip() == 'test message'

    def test_without_shared_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _print_progress('no dir')
        assert not (tmp_path / 'summary.log').exists()
        assert capsys.readouterr().out.strip() == 'no dir'
