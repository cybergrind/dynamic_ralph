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
from coworker_llm.backends.claude_api import DEFAULT_MODEL as CLAUDE_API_DEFAULT_MODEL, ClaudeApiBackend
from coworker_llm.backends.claude_code import DEFAULT_MODEL, ClaudeCodeBackend
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


class TestClaudeCodeBackend:
    def test_argv_includes_prompt_model_and_allowed_tools(self):
        backend = ClaudeCodeBackend(binary='claude', model='claude-haiku-4-5')
        argv = backend.describe(CoworkerRequest(prompt='hi', writes_dir='/work'))
        assert argv[0] == 'claude'
        assert '-p' in argv
        assert 'hi' in argv
        i = argv.index('--model')
        assert argv[i + 1] == 'claude-haiku-4-5'
        i = argv.index('--allowedTools')
        # following entries up to next flag are tool names
        assert 'Read' in argv
        assert 'Write' in argv
        assert 'Edit' in argv

    def test_argv_adds_writes_dir_to_allowed_dirs(self):
        backend = ClaudeCodeBackend()
        argv = backend.describe(CoworkerRequest(prompt='q', writes_dir='/work'))
        i = argv.index('--add-dir')
        assert argv[i + 1] == '/work'

    def test_argv_adds_parent_of_each_read(self):
        backend = ClaudeCodeBackend()
        argv = backend.describe(CoworkerRequest(prompt='q', reads=('/a/b/x.py', '/a/c/y.py')))
        positions = [j for j, t in enumerate(argv) if t == '--add-dir']
        added = {argv[j + 1] for j in positions}
        assert '/a/b' in added
        assert '/a/c' in added

    def test_argv_does_not_double_writes_dir(self):
        backend = ClaudeCodeBackend()
        argv = backend.describe(CoworkerRequest(prompt='q', reads=('/work/x.py',), writes_dir='/work'))
        positions = [j for j, t in enumerate(argv) if t == '--add-dir']
        added = [argv[j + 1] for j in positions]
        assert added.count('/work') == 1

    def test_argv_uses_dangerously_skip_when_unrestricted(self):
        backend = ClaudeCodeBackend(unrestricted=True)
        argv = backend.describe(CoworkerRequest(prompt='q'))
        assert '--dangerously-skip-permissions' in argv
        assert '--allowedTools' not in argv

    def test_run_passes_cwd_to_subprocess(self):
        backend = ClaudeCodeBackend()
        captured: dict[str, object] = {}

        def fake_run(cmd, **kwargs):
            captured['cwd'] = kwargs.get('cwd')
            captured['cmd'] = cmd
            return _completed(stdout='ok')

        with patch('coworker_llm.backends.claude_code.subprocess.run', side_effect=fake_run):
            backend.run(CoworkerRequest(prompt='q', writes_dir='/work'))
        assert captured['cwd'] == '/work'

    def test_run_raises_with_backend_name(self):
        backend = ClaudeCodeBackend()
        with patch(
            'coworker_llm.backends.claude_code.subprocess.run',
            return_value=_completed(returncode=1, stderr='boom'),
        ):
            with pytest.raises(CoworkerError) as exc_info:
                backend.run(CoworkerRequest(prompt='q'))
        assert 'claude-code' in str(exc_info.value)
        assert 'boom' in str(exc_info.value)
        assert exc_info.value.returncode == 1

    def test_from_env_reads_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('COWORKER_CLAUDE_BIN', '/custom/claude')
        monkeypatch.setenv('COWORKER_CLAUDE_MODEL', 'custom-model')
        monkeypatch.setenv('COWORKER_CLAUDE_UNRESTRICTED', '1')
        backend = ClaudeCodeBackend.from_env()
        argv = backend.describe(CoworkerRequest(prompt='q'))
        assert argv[0] == '/custom/claude'
        i = argv.index('--model')
        assert argv[i + 1] == 'custom-model'
        assert '--dangerously-skip-permissions' in argv

    def test_from_env_default_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('COWORKER_CLAUDE_MODEL', raising=False)
        monkeypatch.delenv('COWORKER_CLAUDE_BIN', raising=False)
        monkeypatch.delenv('COWORKER_CLAUDE_UNRESTRICTED', raising=False)
        backend = ClaudeCodeBackend.from_env()
        argv = backend.describe(CoworkerRequest(prompt='q'))
        i = argv.index('--model')
        assert argv[i + 1] == DEFAULT_MODEL

    def test_registry_resolves_claude_code(self):
        backend = get_backend('claude-code')
        assert backend.name == 'claude-code'


class TestClaudeApiBackend:
    def test_name_is_claude_api(self):
        backend = ClaudeApiBackend()
        assert backend.name == 'claude-api'

    def test_default_model_differs_from_claude_code(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('COWORKER_CLAUDE_API_MODEL', raising=False)
        backend = ClaudeApiBackend.from_env()
        argv = backend.describe(CoworkerRequest(prompt='q'))
        i = argv.index('--model')
        assert argv[i + 1] == CLAUDE_API_DEFAULT_MODEL
        assert argv[i + 1] != DEFAULT_MODEL

    def test_from_env_uses_independent_namespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('COWORKER_CLAUDE_API_BIN', '/api/claude')
        monkeypatch.setenv('COWORKER_CLAUDE_API_MODEL', 'api-model')
        # claude-code env vars must NOT bleed in:
        monkeypatch.setenv('COWORKER_CLAUDE_BIN', '/code/claude')
        monkeypatch.setenv('COWORKER_CLAUDE_MODEL', 'code-model')
        backend = ClaudeApiBackend.from_env()
        argv = backend.describe(CoworkerRequest(prompt='q'))
        assert argv[0] == '/api/claude'
        i = argv.index('--model')
        assert argv[i + 1] == 'api-model'

    def test_unrestricted_env_var(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('COWORKER_CLAUDE_API_UNRESTRICTED', '1')
        backend = ClaudeApiBackend.from_env()
        argv = backend.describe(CoworkerRequest(prompt='q'))
        assert '--dangerously-skip-permissions' in argv

    def test_registry_resolves_claude_api(self):
        backend = get_backend('claude-api')
        assert backend.name == 'claude-api'

    def test_inherits_run_translation(self):
        """Today this backend is a thin wrapper over claude -p; smoke-check the run path."""
        backend = ClaudeApiBackend()
        with patch(
            'coworker_llm.backends.claude_code.subprocess.run',
            return_value=_completed(stdout='ok'),
        ):
            result = backend.run(CoworkerRequest(prompt='hi'))
        assert result.stdout == 'ok'


class TestCoworkerResult:
    def test_extras_default_empty(self):
        result = CoworkerResult(stdout='hi')
        assert result.extras == {}

    def test_extras_can_carry_telemetry(self):
        result = CoworkerResult(stdout='hi', extras={'model': 'haiku', 'tokens_in': '42'})
        assert result.extras['model'] == 'haiku'
