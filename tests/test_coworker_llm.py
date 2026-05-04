"""Tests for coworker_llm package: thin wrappers around `opencode run`."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from coworker_llm import ask_llm, extract_chat, llm_write
from coworker_llm.opencode import OpenCodeError, run_opencode


def _completed(returncode: int = 0, stdout: str = '', stderr: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestRunOpencode:
    def test_invokes_opencode_run_with_prompt(self):
        with patch('coworker_llm.opencode.subprocess.run', return_value=_completed(stdout='ok')) as mock_run:
            run_opencode('hello world')
        args, kwargs = mock_run.call_args
        assert args[0] == ['opencode', 'run', 'hello world']
        assert kwargs['capture_output'] is True
        assert kwargs['text'] is True
        assert kwargs.get('check', False) is False

    def test_returns_stdout_on_success(self):
        with patch('coworker_llm.opencode.subprocess.run', return_value=_completed(stdout='reply\n')):
            assert run_opencode('q') == 'reply\n'

    def test_raises_opencode_error_on_failure(self):
        with patch(
            'coworker_llm.opencode.subprocess.run',
            return_value=_completed(returncode=2, stdout='', stderr='boom'),
        ):
            with pytest.raises(OpenCodeError) as exc_info:
                run_opencode('q')
        assert 'boom' in str(exc_info.value)
        assert exc_info.value.returncode == 2


class TestAskLlmFreeform:
    def test_extracts_at_paths_and_strips_prefix(self):
        paths, question = ask_llm.parse_freeform(['what', 'is', 'in', '@foo.py'])
        assert paths == ['foo.py']
        assert '@' not in question
        assert 'foo.py' in question
        assert 'what is in' in question

    def test_multiple_at_paths(self):
        paths, question = ask_llm.parse_freeform(['compare', '@a.py', 'and', '@b.py', 'briefly'])
        assert paths == ['a.py', 'b.py']
        assert 'compare' in question
        assert 'briefly' in question
        assert 'a.py' in question
        assert 'b.py' in question

    def test_no_at_paths_treats_all_as_question(self):
        paths, question = ask_llm.parse_freeform(['just', 'a', 'plain', 'question'])
        assert paths == []
        assert question == 'just a plain question'

    def test_build_prompt_joins_paths(self):
        prompt = ask_llm.build_prompt(['a.py', 'b.py'], 'What IPs are used?')
        assert 'a.py' in prompt
        assert 'b.py' in prompt
        assert 'What IPs are used?' in prompt

    def test_build_prompt_handles_empty_paths(self):
        prompt = ask_llm.build_prompt([], 'A general question?')
        assert 'A general question?' in prompt

    def test_main_calls_run_opencode_with_freeform_argv(self, capsys: pytest.CaptureFixture[str]):
        with patch('coworker_llm.ask_llm.run_opencode', return_value='answer-text') as mock:
            rc = ask_llm.main(['what', 'entrypoints', '@pyproject.toml', 'has'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'pyproject.toml' in prompt
        assert 'entrypoints' in prompt
        assert '@' not in prompt
        assert 'answer-text' in capsys.readouterr().out

    def test_main_reports_error_on_opencode_failure(self, capsys: pytest.CaptureFixture[str]):
        err = OpenCodeError('opencode failed: nope', returncode=2)
        with patch('coworker_llm.ask_llm.run_opencode', side_effect=err):
            rc = ask_llm.main(['what', '@a.py'])
        assert rc != 0
        assert 'nope' in capsys.readouterr().err


class TestAskLlmFlags:
    def test_paths_and_question_flags(self):
        with patch('coworker_llm.ask_llm.run_opencode', return_value='ok') as mock:
            rc = ask_llm.main(['--paths', 'a.py', 'b.py', '--question', 'What IPs?'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'Read these files: a.py, b.py' in prompt
        assert 'What IPs?' in prompt
        assert '--paths' not in prompt
        assert '--question' not in prompt

    def test_question_flag_alone_uses_no_paths(self):
        with patch('coworker_llm.ask_llm.run_opencode', return_value='ok') as mock:
            rc = ask_llm.main(['--question', 'general question'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'general question' in prompt
        assert 'Read these files' not in prompt
        assert '--question' not in prompt


class TestLlmWriteFlags:
    def test_build_prompt_includes_spec_context_target(self):
        prompt = llm_write.build_prompt(spec='write a test', context='ref.py', target='out.py')
        assert 'write a test' in prompt
        assert 'ref.py' in prompt
        assert 'out.py' in prompt

    def test_main_with_flags(self):
        with patch('coworker_llm.llm_write.run_opencode', return_value='ok') as mock:
            rc = llm_write.main(['--spec', 'write a test', '--context', 'ref.py', '--target', 'out.py'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'write a test' in prompt
        assert 'ref.py' in prompt
        assert 'out.py' in prompt

    def test_main_freeform_two_at_paths(self):
        # First @path is context, second @path is target; remaining words form the spec.
        with patch('coworker_llm.llm_write.run_opencode', return_value='ok') as mock:
            rc = llm_write.main(['make', 'a', 'pytest', 'for', '@ref.py', 'into', '@out.py'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'ref.py' in prompt
        assert 'out.py' in prompt
        assert 'make a pytest' in prompt
        assert '@' not in prompt


class TestExtractChat:
    def test_build_prompt_uses_default_question_when_none(self):
        prompt = extract_chat.build_prompt('session.jsonl', output=None, question=None)
        assert 'session.jsonl' in prompt
        assert 'summary' in prompt.lower()

    def test_build_prompt_uses_custom_question(self):
        prompt = extract_chat.build_prompt('s.jsonl', output=None, question='List decisions')
        assert 'List decisions' in prompt
        assert 's.jsonl' in prompt

    def test_main_freeform_extracts_at_path(self):
        with patch('coworker_llm.extract_chat.run_opencode', return_value='ok') as mock:
            rc = extract_chat.main(['@session.jsonl', 'list', 'decisions'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'session.jsonl' in prompt
        assert 'list decisions' in prompt
        assert '@' not in prompt


class TestExtractChatOutputFlag:
    def test_o_short_flag_extracts_to_file(self):
        with patch('coworker_llm.extract_chat.run_opencode', return_value='[done]') as mock:
            rc = extract_chat.main(['session.jsonl', '-o', '/tmp/chat.txt'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'session.jsonl' in prompt
        assert '/tmp/chat.txt' in prompt
        # extraction-mode prompt must instruct opencode to *write* the file
        assert 'write' in prompt.lower()
        assert '-o' not in prompt

    def test_long_flag_output_also_works(self):
        with patch('coworker_llm.extract_chat.run_opencode', return_value='ok') as mock:
            rc = extract_chat.main(['session.jsonl', '--output', '/tmp/x.txt'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert '/tmp/x.txt' in prompt
        assert '--output' not in prompt
        assert 'write' in prompt.lower()

    def test_at_path_with_o_flag(self):
        with patch('coworker_llm.extract_chat.run_opencode', return_value='ok') as mock:
            rc = extract_chat.main(['@session.jsonl', '-o', '/tmp/chat.txt'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'session.jsonl' in prompt
        assert '/tmp/chat.txt' in prompt
        assert '@' not in prompt
        assert '-o' not in prompt

    def test_o_with_question_routes_answer_to_file(self):
        with patch('coworker_llm.extract_chat.run_opencode', return_value='ok') as mock:
            rc = extract_chat.main(['session.jsonl', '-o', '/tmp/x.txt', '--question', 'List decisions'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'List decisions' in prompt
        assert '/tmp/x.txt' in prompt
        assert 'session.jsonl' in prompt
        assert '-o' not in prompt
        assert '--question' not in prompt
