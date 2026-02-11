"""Tests for multi_agent.backend and multi_agent.backends.claude_code."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from multi_agent.backend import (
    _BACKEND_REGISTRY,
    AgentBackend,
    AgentEvent,
    AgentResult,
    get_backend,
    register_backend,
)
from multi_agent.backends.claude_code import ClaudeCodeBackend
from multi_agent.stream import display_agent_event


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure the backend registry is clean before/after each test."""
    saved = dict(_BACKEND_REGISTRY)
    _BACKEND_REGISTRY.clear()
    yield
    _BACKEND_REGISTRY.clear()
    _BACKEND_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# AgentEvent tests
# ---------------------------------------------------------------------------


class TestAgentEvent:
    def test_defaults(self):
        ev = AgentEvent(kind='system')
        assert ev.kind == 'system'
        assert ev.text == ''
        assert ev.raw == {}


# ---------------------------------------------------------------------------
# AgentResult tests
# ---------------------------------------------------------------------------


class TestAgentResult:
    def test_defaults(self):
        r = AgentResult()
        assert r.exit_code == 1
        assert r.num_turns == 0
        assert r.cost_usd == 0.0
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.completion_status == 'unknown'
        assert r.final_response == ''
        assert r.timed_out is False


# ---------------------------------------------------------------------------
# Backend registry tests
# ---------------------------------------------------------------------------


class TestBackendRegistry:
    def test_register_and_retrieve(self):
        register_backend('claude-code', ClaudeCodeBackend)
        backend = get_backend('claude-code')
        assert isinstance(backend, ClaudeCodeBackend)

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match='Unknown agent backend'):
            get_backend('nonexistent-backend')

    def test_default_backend_is_claude_code(self):
        """get_backend() with no args defaults to claude-code."""
        backend = get_backend()
        assert isinstance(backend, ClaudeCodeBackend)

    def test_env_var_override(self):
        register_backend('test-backend', ClaudeCodeBackend)
        with patch.dict(os.environ, {'RALPH_AGENT_BACKEND': 'test-backend'}):
            backend = get_backend()
            assert isinstance(backend, ClaudeCodeBackend)

    def test_explicit_name_overrides_env(self):
        register_backend('explicit', ClaudeCodeBackend)
        with patch.dict(os.environ, {'RALPH_AGENT_BACKEND': 'something-else'}):
            backend = get_backend('explicit')
            assert isinstance(backend, ClaudeCodeBackend)


# ---------------------------------------------------------------------------
# AgentBackend protocol tests
# ---------------------------------------------------------------------------


class TestAgentBackendProtocol:
    def test_claude_code_is_backend(self):
        assert isinstance(ClaudeCodeBackend(), AgentBackend)

    def test_custom_class_satisfies_protocol(self):
        class DummyBackend:
            def build_command(self, prompt, *, system_prompt='', max_turns=None):
                return ['echo', prompt]

            def build_docker_command(self, base_cmd, *, agent_id, workspace):
                return ['docker', 'run', *base_cmd]

            def parse_events(self, lines):
                yield AgentEvent(kind='raw', text='dummy')

            def extract_result(self, events, exit_code):
                return AgentResult(exit_code=exit_code)

        assert isinstance(DummyBackend(), AgentBackend)


# ---------------------------------------------------------------------------
# ClaudeCodeBackend tests
# ---------------------------------------------------------------------------


class TestClaudeCodeBuildCommand:
    def test_basic_command(self):
        backend = ClaudeCodeBackend()
        cmd = backend.build_command('do stuff')
        assert cmd[0] == 'npx'
        assert '@anthropic-ai/claude-code' in cmd
        assert '--dangerously-skip-permissions' in cmd
        assert '--print' in cmd
        assert '--verbose' in cmd
        assert '--output-format' in cmd
        assert 'stream-json' in cmd
        assert cmd[-1] == 'do stuff'

    def test_system_prompt(self):
        backend = ClaudeCodeBackend()
        cmd = backend.build_command('task', system_prompt='be helpful')
        assert '--append-system-prompt' in cmd
        idx = cmd.index('--append-system-prompt')
        assert cmd[idx + 1] == 'be helpful'

    def test_max_turns(self):
        backend = ClaudeCodeBackend()
        cmd = backend.build_command('task', max_turns=10)
        assert '--max-turns' in cmd
        idx = cmd.index('--max-turns')
        assert cmd[idx + 1] == '10'


class TestClaudeCodeBuildDockerCommand:
    @patch('multi_agent.backends.claude_code.image_exists', return_value=True)
    @patch('multi_agent.backends.claude_code.docker_sock_gid', return_value='999')
    def test_basic_docker_command(self, mock_gid, mock_exists):
        backend = ClaudeCodeBackend()
        base_cmd = ['echo', 'hello']
        cmd = backend.build_docker_command(base_cmd, agent_id=1, workspace='/tmp/ws')

        assert cmd[0] == 'docker'
        assert cmd[1] == 'run'
        assert '--rm' in cmd
        # The base_cmd should appear at the end
        assert cmd[-2:] == ['echo', 'hello']
        # Workspace volume
        assert '-v' in cmd
        assert '/tmp/ws:/workspace' in cmd

    @patch('multi_agent.backends.claude_code.image_exists', return_value=False)
    @patch('multi_agent.backends.claude_code.build_image')
    @patch('multi_agent.backends.claude_code.docker_sock_gid', return_value='999')
    def test_builds_image_if_missing(self, mock_gid, mock_build, mock_exists):
        backend = ClaudeCodeBackend()
        backend.build_docker_command(['echo'], agent_id=1, workspace='/tmp')
        mock_build.assert_called_once()

    @patch('multi_agent.backends.claude_code.image_exists', return_value=True)
    @patch('multi_agent.backends.claude_code.docker_sock_gid', return_value='999')
    @patch(
        'multi_agent.backends.claude_code.get_git_author_identity',
        return_value=('Host User', 'host@example.com'),
    )
    def test_git_author_uses_host_identity(self, mock_identity, mock_gid, mock_exists):
        backend = ClaudeCodeBackend()
        cmd = backend.build_docker_command(['echo'], agent_id=1, workspace='/tmp')
        # GIT_AUTHOR_* should use host identity
        author_name_idx = cmd.index('GIT_AUTHOR_NAME=Host User')
        assert cmd[author_name_idx - 1] == '-e'
        author_email_idx = cmd.index('GIT_AUTHOR_EMAIL=host@example.com')
        assert cmd[author_email_idx - 1] == '-e'

    @patch('multi_agent.backends.claude_code.image_exists', return_value=True)
    @patch('multi_agent.backends.claude_code.docker_sock_gid', return_value='999')
    @patch(
        'multi_agent.backends.claude_code.get_git_author_identity',
        return_value=('Host User', 'host@example.com'),
    )
    def test_git_committer_remains_claude_agent(self, mock_identity, mock_gid, mock_exists):
        backend = ClaudeCodeBackend()
        cmd = backend.build_docker_command(['echo'], agent_id=1, workspace='/tmp')
        # GIT_COMMITTER_* should still be Claude Agent / GIT_EMAIL
        assert 'GIT_COMMITTER_NAME=Claude Agent' in cmd
        from multi_agent.constants import GIT_EMAIL

        assert f'GIT_COMMITTER_EMAIL={GIT_EMAIL}' in cmd


class TestClaudeCodeParseEvents:
    def test_system_event(self):
        backend = ClaudeCodeBackend()
        line = json.dumps({'type': 'system', 'model': 'claude-opus-4-20250514'})
        events = list(backend.parse_events(iter([line])))
        assert len(events) == 1
        assert events[0].kind == 'system'
        assert 'claude-opus-4-20250514' in events[0].text

    def test_assistant_text_event(self):
        backend = ClaudeCodeBackend()
        line = json.dumps(
            {
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'Hello world'}]},
            }
        )
        events = list(backend.parse_events(iter([line])))
        assert len(events) == 1
        assert events[0].kind == 'assistant'
        assert events[0].text == 'Hello world'

    def test_tool_use_event(self):
        backend = ClaudeCodeBackend()
        line = json.dumps(
            {
                'type': 'assistant',
                'message': {
                    'content': [
                        {
                            'type': 'tool_use',
                            'name': 'Bash',
                            'input': {'command': 'ls -la'},
                        }
                    ]
                },
            }
        )
        events = list(backend.parse_events(iter([line])))
        assert len(events) == 1
        assert events[0].kind == 'tool_use'
        assert 'Bash' in events[0].text
        assert 'ls -la' in events[0].text

    def test_tool_result_event(self):
        backend = ClaudeCodeBackend()
        line = json.dumps(
            {
                'type': 'user',
                'tool_use_result': 'some output',
            }
        )
        events = list(backend.parse_events(iter([line])))
        assert len(events) == 1
        assert events[0].kind == 'tool_result'
        assert events[0].text == 'some output'

    def test_result_event(self):
        backend = ClaudeCodeBackend()
        line = json.dumps(
            {
                'type': 'result',
                'subtype': 'success',
                'total_cost_usd': 0.05,
                'num_turns': 3,
            }
        )
        events = list(backend.parse_events(iter([line])))
        assert len(events) == 1
        assert events[0].kind == 'result'
        assert 'success' in events[0].text

    def test_non_json_line(self):
        backend = ClaudeCodeBackend()
        events = list(backend.parse_events(iter(['not json at all\n'])))
        assert len(events) == 1
        assert events[0].kind == 'raw'

    def test_empty_lines_skipped(self):
        backend = ClaudeCodeBackend()
        events = list(backend.parse_events(iter(['\n', '  \n', '\t\n'])))
        assert len(events) == 0

    def test_unknown_event_type(self):
        backend = ClaudeCodeBackend()
        line = json.dumps({'type': 'unknown_type', 'data': 'something'})
        events = list(backend.parse_events(iter([line])))
        assert len(events) == 1
        assert events[0].kind == 'raw'


class TestClaudeCodeExtractResult:
    def test_extracts_from_result_event(self):
        backend = ClaudeCodeBackend()
        events = [
            AgentEvent(kind='assistant', text='I did the thing'),
            AgentEvent(
                kind='result',
                text='success',
                raw={
                    'num_turns': 5,
                    'total_cost_usd': 0.123,
                    'input_tokens': 1000,
                    'output_tokens': 500,
                    'subtype': 'end_turn',
                },
            ),
        ]
        result = backend.extract_result(events, exit_code=0)
        assert result.exit_code == 0
        assert result.num_turns == 5
        assert result.cost_usd == 0.123
        assert result.input_tokens == 1000
        assert result.output_tokens == 500
        assert result.completion_status == 'end_turn'
        assert result.final_response == 'I did the thing'

    def test_no_result_event(self):
        backend = ClaudeCodeBackend()
        events = [
            AgentEvent(kind='assistant', text='partial output'),
        ]
        result = backend.extract_result(events, exit_code=1)
        assert result.exit_code == 1
        assert result.final_response == 'partial output'
        assert result.num_turns == 0

    def test_empty_events(self):
        backend = ClaudeCodeBackend()
        result = backend.extract_result([], exit_code=0)
        assert result.exit_code == 0
        assert result.final_response == ''


# ---------------------------------------------------------------------------
# display_agent_event tests
# ---------------------------------------------------------------------------


class TestDisplayAgentEvent:
    def test_system_event(self, capsys):
        display_agent_event(AgentEvent(kind='system', text='session started (model=x)'))
        captured = capsys.readouterr()
        assert '[system]' in captured.err
        assert 'session started' in captured.err

    def test_assistant_event(self, capsys):
        display_agent_event(AgentEvent(kind='assistant', text='hello world'))
        captured = capsys.readouterr()
        assert '[assistant]' in captured.err
        assert 'hello world' in captured.err

    def test_tool_use_event(self, capsys):
        display_agent_event(AgentEvent(kind='tool_use', text='Bash: ls'))
        captured = capsys.readouterr()
        assert '[tool_use]' in captured.err
        assert 'Bash: ls' in captured.err

    def test_tool_result_event(self, capsys):
        display_agent_event(AgentEvent(kind='tool_result', text='file.py'))
        captured = capsys.readouterr()
        assert '[tool_result]' in captured.err

    def test_result_event(self, capsys):
        display_agent_event(AgentEvent(kind='result', text='success (turns=5)'))
        captured = capsys.readouterr()
        assert '[done]' in captured.err

    def test_error_event(self, capsys):
        display_agent_event(AgentEvent(kind='error', text='something went wrong'))
        captured = capsys.readouterr()
        assert '[error]' in captured.err

    def test_raw_event_is_silent(self, capsys):
        display_agent_event(AgentEvent(kind='raw', text='npm warn'))
        captured = capsys.readouterr()
        assert captured.err == ''
