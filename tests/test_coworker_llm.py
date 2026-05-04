"""Tests for coworker_llm package: thin wrappers around `opencode run`."""

from __future__ import annotations

import subprocess
from pathlib import Path
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

    def test_passes_dir_and_attach_files(self):
        with patch('coworker_llm.opencode.subprocess.run', return_value=_completed(stdout='ok')) as mock_run:
            run_opencode('do thing', dir='/work/area', attach=['/in/a.jsonl', '/in/b.txt'])
        cmd = mock_run.call_args.args[0]
        assert cmd[:3] == ['opencode', 'run', 'do thing']
        # --dir must be passed
        i = cmd.index('--dir')
        assert cmd[i + 1] == '/work/area'
        # each attach passed via -f
        f_positions = [j for j, t in enumerate(cmd) if t == '-f']
        assert len(f_positions) == 2
        assert {cmd[j + 1] for j in f_positions} == {'/in/a.jsonl', '/in/b.txt'}


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
        # paths must be passed to opencode as attachments
        assert mock.call_args.kwargs.get('attach') == ('a.py', 'b.py')

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

    def test_main_with_flags(self, tmp_path: Path):
        target = tmp_path / 'out.py'

        def fake(prompt: str, **_kwargs) -> str:
            target.write_text('# generated\n')
            return ''

        with patch('coworker_llm.llm_write.run_opencode', side_effect=fake) as mock:
            rc = llm_write.main(['--spec', 'write a test', '--context', 'ref.py', '--target', str(target)])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'write a test' in prompt
        assert 'ref.py' in prompt
        assert str(target) in prompt
        # opencode invocation must be sandboxed via --dir + -f
        assert mock.call_args.kwargs.get('dir') == str(tmp_path)

    def test_main_freeform_two_at_paths(self, tmp_path: Path):
        target = tmp_path / 'out.py'

        def fake(prompt: str, **_kwargs) -> str:
            target.write_text('# generated\n')
            return ''

        with patch('coworker_llm.llm_write.run_opencode', side_effect=fake) as mock:
            rc = llm_write.main(['make', 'a', 'pytest', 'for', '@ref.py', 'into', f'@{target}'])
        assert rc == 0
        prompt = mock.call_args.args[0]
        assert 'ref.py' in prompt
        assert str(target) in prompt
        assert 'make a pytest' in prompt
        assert '@' not in prompt

    def test_warns_when_target_not_created(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        target = tmp_path / 'never.py'
        with patch('coworker_llm.llm_write.run_opencode', return_value=''):
            rc = llm_write.main(['--spec', 'x', '--context', 'ref.py', '--target', str(target)])
        assert rc != 0
        assert str(target) in capsys.readouterr().err


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


class TestExtractChatQuestionMode:
    """--question routes through opencode (via --dir + -f to bypass permissions)."""

    def test_question_with_o_writes_via_opencode(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        out = tmp_path / 'answer.txt'

        def fake(prompt: str, **_kwargs) -> str:
            out.write_text('answered')
            return ''

        with patch('coworker_llm.extract_chat.run_opencode', side_effect=fake) as mock:
            rc = extract_chat.main([str(src), '-o', str(out), '--question', 'List decisions'])

        assert rc == 0
        kwargs = mock.call_args.kwargs
        assert kwargs.get('dir') == str(tmp_path)
        assert str(src.resolve()) in kwargs.get('attach', ())
        prompt = mock.call_args.args[0]
        assert 'List decisions' in prompt
        assert str(out) in prompt

    def test_question_warns_when_opencode_does_not_create_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        out = tmp_path / 'missing.txt'
        with patch('coworker_llm.extract_chat.run_opencode', return_value=''):
            rc = extract_chat.main([str(src), '-o', str(out), '--question', 'Q'])
        assert rc != 0
        assert str(out) in capsys.readouterr().err


class TestHelpFlag:
    @pytest.mark.parametrize('cmd', [ask_llm, llm_write, extract_chat])
    @pytest.mark.parametrize('flag', ['-h', '--help'])
    def test_help_exits_zero_and_does_not_call_opencode(self, cmd, flag, capsys: pytest.CaptureFixture[str]):
        with patch.object(cmd, 'run_opencode', side_effect=AssertionError('help must not call opencode')):
            rc = cmd.main([flag])
        assert rc == 0
        out = capsys.readouterr().out
        assert 'usage' in out.lower()


class TestExtractChatOutputConfirmation:
    """Local mode confirms byte count after writing."""

    def test_confirms_when_file_written(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        src = tmp_path / 's.jsonl'
        src.write_text(
            '{"type":"user","message":{"role":"user","content":"' + 'x' * 100 + '"}}\n',
        )
        out = tmp_path / 'chat.txt'
        rc = extract_chat.main([str(src), '-o', str(out)])
        captured = capsys.readouterr()
        all_output = captured.out + captured.err
        assert rc == 0
        assert str(out) in all_output
        assert 'wrote' in all_output.lower()
        assert out.is_file()


class TestExtractTranscript:
    """Default mode parses JSONL locally; never calls opencode (avoids context limits)."""

    def test_simple_user_assistant_exchange(self, tmp_path: Path):
        f = tmp_path / 's.jsonl'
        f.write_text(
            '{"type":"user","message":{"role":"user","content":"hi"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"hello back"}}\n',
        )
        result = extract_chat.extract_transcript(str(f))
        assert 'hi' in result
        assert 'hello back' in result
        assert 'USER' in result
        assert 'ASSISTANT' in result

    def test_content_as_text_blocks(self, tmp_path: Path):
        f = tmp_path / 's.jsonl'
        f.write_text(
            '{"type":"assistant","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"the answer is 42"}]}}\n',
        )
        assert 'the answer is 42' in extract_chat.extract_transcript(str(f))

    def test_tool_use_block_becomes_placeholder(self, tmp_path: Path):
        f = tmp_path / 's.jsonl'
        f.write_text(
            '{"type":"assistant","message":{"role":"assistant",'
            '"content":[{"type":"tool_use","name":"Bash","input":{"command":"ls"}}]}}\n',
        )
        result = extract_chat.extract_transcript(str(f))
        assert 'tool_use' in result.lower()
        assert 'Bash' in result

    def test_skips_invalid_json_lines(self, tmp_path: Path):
        f = tmp_path / 's.jsonl'
        f.write_text(
            '{"type":"user","message":{"role":"user","content":"first"}}\n'
            'not-json-garbage\n'
            '{"type":"assistant","message":{"role":"assistant","content":"second"}}\n',
        )
        result = extract_chat.extract_transcript(str(f))
        assert 'first' in result
        assert 'second' in result

    def test_skips_non_message_types(self, tmp_path: Path):
        f = tmp_path / 's.jsonl'
        f.write_text(
            '{"type":"permission-mode","permissionMode":"default"}\n'
            '{"type":"user","message":{"role":"user","content":"actual content"}}\n'
            '{"type":"file-history-snapshot","messageId":"x"}\n',
        )
        result = extract_chat.extract_transcript(str(f))
        assert 'actual content' in result
        assert 'permission-mode' not in result
        assert 'file-history-snapshot' not in result


class TestExtractChatLocalMode:
    def test_main_writes_to_o_locally(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hello there"}}\n')
        out = tmp_path / 'out.txt'
        rc = extract_chat.main([str(src), '-o', str(out)])
        assert rc == 0
        assert out.is_file()
        assert 'hello there' in out.read_text()

    def test_main_local_does_not_call_opencode(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        out = tmp_path / 'out.txt'
        with patch(
            'coworker_llm.extract_chat.run_opencode',
            side_effect=AssertionError('local mode must not call opencode'),
        ):
            rc = extract_chat.main([str(src), '-o', str(out)])
        assert rc == 0

    def test_main_no_o_prints_to_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hello stdout"}}\n')
        with patch(
            'coworker_llm.extract_chat.run_opencode',
            side_effect=AssertionError('local mode must not call opencode'),
        ):
            rc = extract_chat.main([str(src)])
        assert rc == 0
        assert 'hello stdout' in capsys.readouterr().out

    def test_main_question_still_routes_through_opencode(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        with patch('coworker_llm.extract_chat.run_opencode', return_value='answer-text') as mock:
            rc = extract_chat.main([str(src), '--question', 'what was discussed?'])
        assert rc == 0
        mock.assert_called_once()
