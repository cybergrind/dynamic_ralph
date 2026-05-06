"""Tests for the coworker_llm backend protocol and OpenCodeBackend."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from coworker_llm.backend import (
    CoworkerBackend,
    CoworkerError,
    CoworkerRequest,
    CoworkerResult,
    get_backend,
    list_backends,
)
from coworker_llm.backends.opencode import OpenCodeBackend


def _completed(returncode: int = 0, stdout: str = '', stderr: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRegistry:
    def test_list_backends_includes_opencode(self):
        assert 'opencode' in list_backends()

    def test_default_backend_is_opencode(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('COWORKER_BACKEND', raising=False)
        backend = get_backend()
        assert backend.name == 'opencode'

    def test_env_var_selects_backend(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('COWORKER_BACKEND', 'opencode')
        backend = get_backend()
        assert backend.name == 'opencode'

    def test_explicit_name_beats_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('COWORKER_BACKEND', 'does-not-exist')
        backend = get_backend('opencode')
        assert backend.name == 'opencode'

    def test_unknown_backend_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('COWORKER_BACKEND', raising=False)
        with pytest.raises(CoworkerError) as exc_info:
            get_backend('does-not-exist')
        assert 'does-not-exist' in str(exc_info.value)

    def test_returned_backend_satisfies_protocol(self):
        backend = get_backend('opencode')
        assert isinstance(backend, CoworkerBackend)


class TestOpenCodeBackend:
    def test_argv_translates_request(self):
        backend = OpenCodeBackend(binary='opencode')
        req = CoworkerRequest(
            prompt='hello',
            reads=('/in/a.py', '/in/b.py'),
            writes_dir='/work',
        )
        argv = backend.describe(req)
        assert argv[:3] == ['opencode', 'run', 'hello']
        i = argv.index('--dir')
        assert argv[i + 1] == '/work'
        f_positions = [j for j, t in enumerate(argv) if t == '-f']
        assert {argv[j + 1] for j in f_positions} == {'/in/a.py', '/in/b.py'}

    def test_argv_omits_dir_when_no_writes(self):
        backend = OpenCodeBackend(binary='opencode')
        req = CoworkerRequest(prompt='q', reads=('/in/a.py',))
        argv = backend.describe(req)
        assert '--dir' not in argv

    def test_argv_omits_attachments_when_no_reads(self):
        backend = OpenCodeBackend(binary='opencode')
        req = CoworkerRequest(prompt='q')
        argv = backend.describe(req)
        assert '-f' not in argv

    def test_run_returns_stdout_on_success(self):
        backend = OpenCodeBackend(binary='opencode')
        with patch(
            'coworker_llm.backends.opencode.subprocess.run',
            return_value=_completed(stdout='reply\n'),
        ):
            result = backend.run(CoworkerRequest(prompt='hi'))
        assert result.stdout == 'reply\n'

    def test_run_raises_coworker_error_on_failure(self):
        backend = OpenCodeBackend(binary='opencode')
        with patch(
            'coworker_llm.backends.opencode.subprocess.run',
            return_value=_completed(returncode=2, stderr='boom'),
        ):
            with pytest.raises(CoworkerError) as exc_info:
                backend.run(CoworkerRequest(prompt='hi'))
        assert 'boom' in str(exc_info.value)
        assert exc_info.value.returncode == 2

    def test_error_message_uses_backend_name_not_brand(self):
        backend = OpenCodeBackend(binary='opencode')
        with patch(
            'coworker_llm.backends.opencode.subprocess.run',
            return_value=_completed(returncode=1, stderr='err'),
        ):
            with pytest.raises(CoworkerError) as exc_info:
                backend.run(CoworkerRequest(prompt='hi'))
        assert backend.name in str(exc_info.value)

    def test_from_env_respects_binary_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('COWORKER_OPENCODE_BIN', '/custom/opencode')
        backend = OpenCodeBackend.from_env()
        argv = backend.describe(CoworkerRequest(prompt='q'))
        assert argv[0] == '/custom/opencode'

    def test_is_available_uses_which(self):
        backend = OpenCodeBackend(binary='opencode')
        with patch('coworker_llm.backends.opencode.shutil.which', return_value='/usr/bin/opencode'):
            assert backend.is_available() is True
        with patch('coworker_llm.backends.opencode.shutil.which', return_value=None):
            assert backend.is_available() is False


class TestCoworkerResult:
    def test_extras_default_empty(self):
        result = CoworkerResult(stdout='hi')
        assert result.extras == {}

    def test_extras_can_carry_telemetry(self):
        result = CoworkerResult(stdout='hi', extras={'model': 'haiku', 'tokens_in': '42'})
        assert result.extras['model'] == 'haiku'
