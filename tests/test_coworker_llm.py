"""Tests for coworker_llm CLIs (ask_llm, llm_write, extract_chat).

Backend invocation is faked out — real subprocess calls live in
test_coworker_llm_integration.py.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from coworker_llm import ask_llm, extract_chat, llm_write
from coworker_llm.backend import CoworkerError, CoworkerRequest, CoworkerResult


@dataclass
class FakeBackend:
    """Stand-in backend that records every CoworkerRequest it receives."""

    name: str = 'fake'
    stdout: str = ''
    side_effect: Callable[[CoworkerRequest], CoworkerResult] | None = None
    raise_error: CoworkerError | None = None
    calls: list[CoworkerRequest] = field(default_factory=list)

    def run(self, request: CoworkerRequest) -> CoworkerResult:
        self.calls.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if self.side_effect is not None:
            return self.side_effect(request)
        return CoworkerResult(stdout=self.stdout)

    def is_available(self) -> bool:
        return True

    @property
    def request(self) -> CoworkerRequest:
        assert self.calls, 'backend.run() was not called'
        return self.calls[-1]


def _patch_backend(module, fake: FakeBackend):
    return patch.object(module, 'get_backend', return_value=fake)


def _completed(returncode: int = 0, stdout: str = '', stderr: str = '') -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestAskLlmFreeform:
    def test_extracts_at_paths_and_strips_prefix(self):
        paths, question, backend = ask_llm.parse_freeform(['what', 'is', 'in', '@foo.py'])
        assert paths == ['foo.py']
        assert '@' not in question
        assert 'foo.py' in question
        assert 'what is in' in question
        assert backend is None

    def test_multiple_at_paths(self):
        paths, question, _ = ask_llm.parse_freeform(['compare', '@a.py', 'and', '@b.py', 'briefly'])
        assert paths == ['a.py', 'b.py']
        assert 'compare' in question
        assert 'briefly' in question
        assert 'a.py' in question
        assert 'b.py' in question

    def test_no_at_paths_treats_all_as_question(self):
        paths, question, _ = ask_llm.parse_freeform(['just', 'a', 'plain', 'question'])
        assert paths == []
        assert question == 'just a plain question'

    def test_freeform_recognizes_backend_flag(self):
        paths, question, backend = ask_llm.parse_freeform(['--backend', 'fake', 'what', '@x.py'])
        assert paths == ['x.py']
        assert backend == 'fake'
        assert '--backend' not in question
        assert 'fake' not in question

    def test_build_prompt_joins_paths(self):
        prompt = ask_llm.build_prompt(['a.py', 'b.py'], 'What IPs are used?')
        assert 'a.py' in prompt
        assert 'b.py' in prompt
        assert 'What IPs are used?' in prompt

    def test_build_prompt_handles_empty_paths(self):
        prompt = ask_llm.build_prompt([], 'A general question?')
        assert 'A general question?' in prompt

    def test_main_calls_backend_with_freeform_argv(self, capsys: pytest.CaptureFixture[str]):
        fake = FakeBackend(stdout='answer-text')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['what', 'entrypoints', '@pyproject.toml', 'has'])
        assert rc == 0
        assert 'pyproject.toml' in fake.request.prompt
        assert 'entrypoints' in fake.request.prompt
        assert '@' not in fake.request.prompt
        assert fake.request.reads == ('pyproject.toml',)
        assert 'answer-text' in capsys.readouterr().out

    def test_main_reports_error_on_backend_failure(self, capsys: pytest.CaptureFixture[str]):
        fake = FakeBackend(raise_error=CoworkerError('coworker failed: nope', returncode=2))
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['what', '@a.py'])
        assert rc != 0
        assert 'nope' in capsys.readouterr().err


class TestAskLlmFlags:
    def test_paths_and_question_flags(self):
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', 'a.py', 'b.py', '--question', 'What IPs?'])
        assert rc == 0
        assert 'Read these files: a.py, b.py' in fake.request.prompt
        assert 'What IPs?' in fake.request.prompt
        assert '--paths' not in fake.request.prompt
        assert '--question' not in fake.request.prompt
        assert fake.request.reads == ('a.py', 'b.py')

    def test_question_flag_alone_uses_no_paths(self):
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--question', 'general question'])
        assert rc == 0
        assert 'general question' in fake.request.prompt
        assert 'Read these files' not in fake.request.prompt
        assert fake.request.reads == ()

    def test_main_with_question_file(self, tmp_path: Path):
        question_file = tmp_path / 'q.md'
        question_file.write_text(
            'Find any reference to `BANANA` and $(SECRET) in these files.\nReport line numbers.',
        )
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', 'a.py', '--question-file', str(question_file)])
        assert rc == 0
        assert '`BANANA`' in fake.request.prompt
        assert '$(SECRET)' in fake.request.prompt
        assert 'a.py' in fake.request.prompt
        assert fake.request.reads == ('a.py',)

    def test_question_and_question_file_are_mutually_exclusive(self, tmp_path: Path):
        qf = tmp_path / 'q.md'
        qf.write_text('hello')
        rc = ask_llm.main(['--question', 'x', '--question-file', str(qf)])
        assert rc == 2

    def test_question_or_question_file_is_required(self, tmp_path: Path):
        rc = ask_llm.main(['--paths', 'a.py'])
        assert rc == 2

    def test_backend_flag_overrides_default(self):
        fake = FakeBackend(name='alt')
        with patch('coworker_llm.ask_llm.get_backend', return_value=fake) as mock:
            rc = ask_llm.main(['--paths', 'a.py', '--question', 'q', '--backend', 'alt'])
        assert rc == 0
        mock.assert_called_with('alt')

    def test_paths_from_file_reads_one_per_line(self, tmp_path: Path):
        path_list = tmp_path / 'paths.txt'
        path_list.write_text('first file.py\nsecond.py\n# a comment\n\nthird.py\n')
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths-from', str(path_list), '--question', 'q?'])
        assert rc == 0
        assert fake.request.reads == ('first file.py', 'second.py', 'third.py')
        assert 'first file.py' in fake.request.prompt
        assert 'second.py' in fake.request.prompt

    def test_paths_from_file_combines_with_paths(self, tmp_path: Path):
        path_list = tmp_path / 'paths.txt'
        path_list.write_text('extra1.py\nextra2.py\n')
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(
                ['--paths', 'a.py', '--paths-from', str(path_list), '--question', 'q?'],
            )
        assert rc == 0
        assert fake.request.reads == ('a.py', 'extra1.py', 'extra2.py')

    def test_paths_from_stdin(self, monkeypatch: pytest.MonkeyPatch):
        import io

        monkeypatch.setattr('sys.stdin', io.StringIO('s1.py\ns2.py\n'))
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths-from', '-', '--question', 'q?'])
        assert rc == 0
        assert fake.request.reads == ('s1.py', 's2.py')

    def test_max_words_appends_word_limit_to_prompt(self):
        fake = FakeBackend()
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', 'a.py', '--question', 'q?', '--max-words', '1500'])
        assert rc == 0
        prompt = fake.request.prompt
        assert '1500' in prompt
        assert 'word' in prompt.lower()


class TestAskLlmLatencyLog:
    """Every call emits a one-line stderr observability summary."""

    def test_logs_wall_in_out_to_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        f = tmp_path / 'big.log'
        f.write_text('x' * 8000)
        fake = FakeBackend(stdout='answer body')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', str(f), '--question', 'q?'])
        assert rc == 0
        err = capsys.readouterr().err
        assert 'ask-llm:' in err
        assert 'wall=' in err
        assert 'in=' in err
        assert 'out=' in err

    def test_log_handles_missing_path_without_crashing(self, capsys: pytest.CaptureFixture[str]):
        fake = FakeBackend(stdout='answer')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', '/no/such/file.log', '--question', 'q?'])
        assert rc == 0
        err = capsys.readouterr().err
        assert 'ask-llm:' in err
        assert 'wall=' in err


class TestAskLlmPreflightWarning:
    """Large inputs without --max-words get a one-line stderr warning."""

    def test_warns_when_inputs_large_and_no_max_words(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        f = tmp_path / 'huge.log'
        f.write_text('x' * 60000)
        fake = FakeBackend(stdout='ok')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', str(f), '--question', 'q?'])
        assert rc == 0
        err = capsys.readouterr().err
        assert 'max-words' in err
        assert 'warning' in err.lower() or 'warn' in err.lower()

    def test_no_warning_when_max_words_set(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        f = tmp_path / 'huge.log'
        f.write_text('x' * 60000)
        fake = FakeBackend(stdout='ok')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(
                ['--paths', str(f), '--question', 'q?', '--max-words', '1500'],
            )
        assert rc == 0
        err = capsys.readouterr().err
        assert 'warning' not in err.lower()

    def test_no_warning_when_inputs_small(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        f = tmp_path / 'small.log'
        f.write_text('x' * 1000)
        fake = FakeBackend(stdout='ok')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(['--paths', str(f), '--question', 'q?'])
        assert rc == 0
        err = capsys.readouterr().err
        assert 'warning' not in err.lower()

    def test_no_warn_flag_suppresses_warning(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        f = tmp_path / 'huge.log'
        f.write_text('x' * 60000)
        fake = FakeBackend(stdout='ok')
        with _patch_backend(ask_llm, fake):
            rc = ask_llm.main(
                ['--paths', str(f), '--question', 'q?', '--no-warn'],
            )
        assert rc == 0
        err = capsys.readouterr().err
        assert 'warning' not in err.lower()


class TestLlmWriteFlags:
    def test_build_prompt_includes_spec_context_target(self):
        prompt = llm_write.build_prompt(spec='write a test', context='ref.py', target='out.py')
        assert 'write a test' in prompt
        assert 'ref.py' in prompt
        assert 'out.py' in prompt

    def test_main_with_flags(self, tmp_path: Path):
        target = tmp_path / 'out.py'

        def materialize(req: CoworkerRequest) -> CoworkerResult:
            target.write_text('# generated\n')
            return CoworkerResult(stdout='')

        fake = FakeBackend(side_effect=materialize)
        with _patch_backend(llm_write, fake):
            rc = llm_write.main(['--spec', 'write a test', '--context', 'ref.py', '--target', str(target)])
        assert rc == 0
        assert 'write a test' in fake.request.prompt
        assert 'ref.py' in fake.request.prompt
        assert str(target) in fake.request.prompt
        assert fake.request.writes_dir == str(tmp_path)
        assert fake.request.reads == (str(Path('ref.py').resolve()),)
        assert fake.request.expected_target == str(target.resolve())

    def test_main_freeform_two_at_paths(self, tmp_path: Path):
        target = tmp_path / 'out.py'

        def materialize(req: CoworkerRequest) -> CoworkerResult:
            target.write_text('# generated\n')
            return CoworkerResult(stdout='')

        fake = FakeBackend(side_effect=materialize)
        with _patch_backend(llm_write, fake):
            rc = llm_write.main(['make', 'a', 'pytest', 'for', '@ref.py', 'into', f'@{target}'])
        assert rc == 0
        assert 'ref.py' in fake.request.prompt
        assert str(target) in fake.request.prompt
        assert 'make a pytest' in fake.request.prompt
        assert '@' not in fake.request.prompt

    def test_warns_when_target_not_created(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        target = tmp_path / 'never.py'
        fake = FakeBackend()
        with _patch_backend(llm_write, fake):
            rc = llm_write.main(['--spec', 'x', '--context', 'ref.py', '--target', str(target)])
        assert rc != 0
        assert str(target) in capsys.readouterr().err

    def test_main_with_spec_file(self, tmp_path: Path):
        target = tmp_path / 'out.py'
        spec_file = tmp_path / 'spec.md'
        spec_file.write_text('Use `backticks` and $(subshells) — no shell hazards here.')

        def materialize(req: CoworkerRequest) -> CoworkerResult:
            target.write_text('# generated\n')
            return CoworkerResult(stdout='')

        fake = FakeBackend(side_effect=materialize)
        with _patch_backend(llm_write, fake):
            rc = llm_write.main(
                ['--spec-file', str(spec_file), '--context', 'ref.py', '--target', str(target)],
            )
        assert rc == 0
        assert '`backticks`' in fake.request.prompt
        assert '$(subshells)' in fake.request.prompt
        assert 'ref.py' in fake.request.prompt

    def test_spec_and_spec_file_are_mutually_exclusive(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        spec_file = tmp_path / 'spec.md'
        spec_file.write_text('hello')
        target = tmp_path / 'out.py'
        rc = llm_write.main(
            ['--spec', 'x', '--spec-file', str(spec_file), '--context', 'ref.py', '--target', str(target)],
        )
        assert rc == 2

    def test_spec_or_spec_file_is_required(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        rc = llm_write.main(['--context', 'ref.py', '--target', str(tmp_path / 'out.py')])
        assert rc == 2

    def test_backend_flag_overrides_default(self, tmp_path: Path):
        target = tmp_path / 'out.py'

        def materialize(req: CoworkerRequest) -> CoworkerResult:
            target.write_text('# x\n')
            return CoworkerResult(stdout='')

        fake = FakeBackend(side_effect=materialize)
        with patch('coworker_llm.llm_write.get_backend', return_value=fake) as mock:
            rc = llm_write.main(
                [
                    '--spec',
                    's',
                    '--context',
                    'r.py',
                    '--target',
                    str(target),
                    '--backend',
                    'alt',
                ]
            )
        assert rc == 0
        mock.assert_called_with('alt')


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
        fake = FakeBackend()
        with _patch_backend(extract_chat, fake):
            rc = extract_chat.main(['@session.jsonl', 'list', 'decisions'])
        assert rc == 0
        assert 'session.jsonl' in fake.request.prompt
        assert 'list decisions' in fake.request.prompt
        assert '@' not in fake.request.prompt


class TestExtractChatQuestionMode:
    """--question routes through the configured backend (with reads/writes_dir set)."""

    def test_question_with_o_routes_through_backend(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        out = tmp_path / 'answer.txt'

        def materialize(req: CoworkerRequest) -> CoworkerResult:
            out.write_text('answered')
            return CoworkerResult(stdout='')

        fake = FakeBackend(side_effect=materialize)
        with _patch_backend(extract_chat, fake):
            rc = extract_chat.main([str(src), '-o', str(out), '--question', 'List decisions'])

        assert rc == 0
        assert fake.request.writes_dir == str(tmp_path)
        assert str(src.resolve()) in fake.request.reads
        assert 'List decisions' in fake.request.prompt
        assert str(out) in fake.request.prompt

    def test_question_warns_when_backend_does_not_create_file(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        out = tmp_path / 'missing.txt'
        fake = FakeBackend()
        with _patch_backend(extract_chat, fake):
            rc = extract_chat.main([str(src), '-o', str(out), '--question', 'Q'])
        assert rc != 0
        assert str(out) in capsys.readouterr().err

    def test_backend_flag_overrides_default(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        fake = FakeBackend(stdout='reply')
        with patch('coworker_llm.extract_chat.get_backend', return_value=fake) as mock:
            rc = extract_chat.main([str(src), '--question', 'Q', '--backend', 'alt'])
        assert rc == 0
        mock.assert_called_with('alt')


class TestHelpFlag:
    @pytest.mark.parametrize('cmd', [ask_llm, llm_write, extract_chat])
    @pytest.mark.parametrize('flag', ['-h', '--help'])
    def test_help_exits_zero_and_does_not_call_backend(self, cmd, flag, capsys: pytest.CaptureFixture[str]):
        with patch.object(cmd, 'get_backend', side_effect=AssertionError('help must not call backend')):
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
    """Default mode parses JSONL locally; never calls a backend (avoids context limits)."""

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

    def test_main_local_does_not_call_backend(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        out = tmp_path / 'out.txt'
        with patch(
            'coworker_llm.extract_chat.get_backend',
            side_effect=AssertionError('local mode must not call backend'),
        ):
            rc = extract_chat.main([str(src), '-o', str(out)])
        assert rc == 0

    def test_main_no_o_prints_to_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hello stdout"}}\n')
        with patch(
            'coworker_llm.extract_chat.get_backend',
            side_effect=AssertionError('local mode must not call backend'),
        ):
            rc = extract_chat.main([str(src)])
        assert rc == 0
        assert 'hello stdout' in capsys.readouterr().out

    def test_main_question_still_routes_through_backend(self, tmp_path: Path):
        src = tmp_path / 's.jsonl'
        src.write_text('{"type":"user","message":{"role":"user","content":"hi"}}\n')
        fake = FakeBackend(stdout='answer-text')
        with _patch_backend(extract_chat, fake):
            rc = extract_chat.main([str(src), '--question', 'what was discussed?'])
        assert rc == 0
        assert len(fake.calls) == 1
