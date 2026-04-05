"""Tests for multi_agent.parallel — parallel agent execution engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from multi_agent.backend import AgentEvent, AgentResult
from multi_agent.parallel import (
    _RETAINED_KINDS,
    _SubprocessWatchdog,
    launch_parallel_agents,
)


# ---------------------------------------------------------------------------
# FakeBackend — satisfies AgentBackend protocol with scripted responses
# ---------------------------------------------------------------------------


class FakeBackend:
    """A backend that spawns real subprocesses running a Python script.

    The script prints JSON events to stdout and then exits.  This lets us
    test the full subprocess lifecycle (Popen, event parsing, stderr drain,
    watchdog) without requiring ``claude -p``.
    """

    def __init__(self, script: str = '', *, exit_code: int = 0) -> None:
        self._script = script
        self._exit_code = exit_code

    def build_command(
        self, prompt: str, *, system_prompt: str = '', max_turns: int | None = None, json_schema: dict | None = None
    ) -> list[str]:
        # Encode the script inline so Popen can run it directly.
        return [sys.executable, '-c', self._script]

    def build_docker_command(self, base_cmd: list[str], *, agent_id: int, workspace: str) -> list[str]:
        return base_cmd

    def parse_events(self, lines: Iterator[str]) -> Iterator[AgentEvent]:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                yield AgentEvent(
                    kind=data.get('kind', 'raw'),
                    text=data.get('text', ''),
                    raw=data,
                )
            except json.JSONDecodeError:
                yield AgentEvent(kind='raw', text=line)

    def extract_result(self, events: list[AgentEvent], exit_code: int) -> AgentResult:
        parts = [ev.text for ev in events if ev.kind == 'assistant']
        return AgentResult(
            exit_code=exit_code,
            final_response=parts[-1] if parts else '',
            full_response='\n\n'.join(parts),
            completion_status='end_turn' if exit_code == 0 else 'error',
        )


# ---------------------------------------------------------------------------
# Helpers for building inline Python scripts
# ---------------------------------------------------------------------------


def _make_event_script(*events: dict, sleep_before: float = 0, exit_code: int = 0) -> str:
    """Return a Python script that prints JSON events to stdout and exits."""
    lines = []
    if sleep_before:
        lines.append(f'import time; time.sleep({sleep_before})')
    lines.append('import json, sys')
    for ev in events:
        lines.append(f'print(json.dumps({ev!r}), flush=True)')
    if exit_code:
        lines.append(f'sys.exit({exit_code})')
    return '; '.join(lines)


def _make_hang_script() -> str:
    """Return a Python script that prints one event then blocks forever."""
    return textwrap.dedent("""\
        import json, sys, time
        print(json.dumps({"kind": "assistant", "text": "started"}), flush=True)
        time.sleep(3600)
    """)


# ---------------------------------------------------------------------------
# TestRetainedKinds
# ---------------------------------------------------------------------------


class TestRetainedKinds:
    def test_contents(self):
        assert _RETAINED_KINDS == frozenset({'assistant', 'result', 'system', 'error'})


# ---------------------------------------------------------------------------
# TestSubprocessWatchdog
# ---------------------------------------------------------------------------


class TestSubprocessWatchdog:
    def test_fires_after_timeout(self):
        """Watchdog terminates a hanging process after the deadline."""
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(3600)'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wd = _SubprocessWatchdog(proc, timeout=0.5)
            wd.start()
            # Wait for the watchdog to fire
            proc.wait(timeout=5)
            assert wd.fired is True
            assert proc.returncode is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_cancel_prevents_firing(self):
        """Cancelling before the deadline prevents termination."""
        proc = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(3600)'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wd = _SubprocessWatchdog(proc, timeout=5.0)
            wd.start()
            wd.cancel()
            # Give the watchdog thread time to process the cancel
            time.sleep(0.05)
            assert wd.fired is False
            assert proc.poll() is None  # process still running
        finally:
            proc.kill()
            proc.wait()

    def test_no_fire_on_normal_exit(self):
        """Watchdog does not fire if process exits before deadline."""
        proc = subprocess.Popen(
            [sys.executable, '-c', 'pass'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        proc.wait()
        wd = _SubprocessWatchdog(proc, timeout=5.0)
        wd.start()
        # Wait a bit — should not fire since process already exited
        time.sleep(0.05)
        wd.cancel()
        assert wd.fired is False

    def test_escalates_to_kill(self):
        """Watchdog escalates to SIGKILL when SIGTERM is ignored."""
        # Script that traps SIGTERM and ignores it
        script = textwrap.dedent("""\
            import signal, time
            signal.signal(signal.SIGTERM, lambda *a: None)
            time.sleep(3600)
        """)
        proc = subprocess.Popen(
            [sys.executable, '-c', script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            wd = _SubprocessWatchdog(proc, timeout=0.5, grace_period=0.5)
            wd.start()
            proc.wait(timeout=5)
            assert wd.fired is True
            assert proc.returncode is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


# ---------------------------------------------------------------------------
# TestLaunchParallelAgents
# ---------------------------------------------------------------------------


class TestLaunchParallelAgents:
    def test_basic_parallel_execution(self, tmp_path: Path):
        """Multiple agents run and return results keyed by label."""
        script = _make_event_script(
            {'kind': 'assistant', 'text': 'hello'},
            {'kind': 'result', 'text': 'done'},
        )
        backend = FakeBackend(script)
        prompts = {'agent-a': 'do task A', 'agent-b': 'do task B'}

        results = launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        assert set(results.keys()) == {'agent-a', 'agent-b'}
        for label, result in results.items():
            assert result.exit_code == 0
            assert result.completion_status == 'end_turn'
            assert result.final_response == 'hello'

    def test_log_files_created(self, tmp_path: Path):
        """Each agent gets a .jsonl and .stderr.log file."""
        script = _make_event_script(
            {'kind': 'assistant', 'text': 'logged'},
        )
        backend = FakeBackend(script)
        prompts = {'alpha': 'prompt alpha'}

        launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        assert (tmp_path / 'alpha.jsonl').exists()
        assert (tmp_path / 'alpha.stderr.log').exists()

        # Verify the jsonl log contains the event
        log_content = (tmp_path / 'alpha.jsonl').read_text()
        assert 'assistant' in log_content

    def test_event_filtering(self, tmp_path: Path):
        """Only _RETAINED_KINDS events are passed to extract_result()."""
        script = _make_event_script(
            {'kind': 'system', 'text': 'session start'},
            {'kind': 'assistant', 'text': 'response'},
            {'kind': 'tool_use', 'text': 'Bash: ls'},
            {'kind': 'tool_result', 'text': 'file.py contents...'},
            {'kind': 'result', 'text': 'done'},
        )

        # Track what events extract_result receives
        received_events: list[list[AgentEvent]] = []

        class TrackingBackend(FakeBackend):
            def extract_result(self, events, exit_code):
                received_events.append(events)
                return super().extract_result(events, exit_code)

        backend = TrackingBackend(script)
        prompts = {'filter-test': 'test prompt'}

        launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        # extract_result should only get retained kinds
        assert len(received_events) == 1
        kinds = {ev.kind for ev in received_events[0]}
        assert kinds <= _RETAINED_KINDS
        assert 'tool_use' not in kinds
        assert 'tool_result' not in kinds

    def test_all_events_written_to_disk(self, tmp_path: Path):
        """All events (including tool events) are written to the disk log."""
        script = _make_event_script(
            {'kind': 'assistant', 'text': 'response'},
            {'kind': 'tool_use', 'text': 'Bash: ls'},
            {'kind': 'tool_result', 'text': 'file.py'},
            {'kind': 'result', 'text': 'done'},
        )
        backend = FakeBackend(script)
        prompts = {'disk-test': 'test prompt'}

        launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        log_content = (tmp_path / 'disk-test.jsonl').read_text()
        assert 'tool_use' in log_content
        assert 'tool_result' in log_content
        assert 'assistant' in log_content

    def test_timeout_via_watchdog(self, tmp_path: Path):
        """Agents that hang are terminated by the watchdog."""
        script = _make_hang_script()
        backend = FakeBackend(script)
        prompts = {'hang-agent': 'do something'}

        results = launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=0.5)

        assert 'hang-agent' in results
        result = results['hang-agent']
        assert result.timed_out is True

    def test_crash_handling(self, tmp_path: Path):
        """An agent that raises produces a crashed AgentResult."""

        class CrashingBackend(FakeBackend):
            def build_command(self, prompt, *, system_prompt='', max_turns=None):
                # Return a command that does not exist to trigger an exception
                return ['__nonexistent_binary_12345__']

        backend = CrashingBackend()
        prompts = {'crash-agent': 'do something'}

        results = launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        assert 'crash-agent' in results
        result = results['crash-agent']
        assert result.exit_code == 1
        assert result.completion_status == 'crashed'

    def test_strips_claudecode_env(self, tmp_path: Path):
        """CLAUDECODE is stripped from the environment passed to agents."""
        # Script that checks for CLAUDECODE in its environment
        script = textwrap.dedent("""\
            import json, os, sys
            has_claudecode = 'CLAUDECODE' in os.environ
            print(json.dumps({"kind": "assistant", "text": str(has_claudecode)}), flush=True)
        """)
        backend = FakeBackend(script)
        prompts = {'env-test': 'check env'}

        with patch.dict(os.environ, {'CLAUDECODE': '1'}):
            results = launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        result = results['env-test']
        assert result.final_response == 'False'

    def test_stderr_captured_to_log(self, tmp_path: Path):
        """stderr output is captured to per-agent .stderr.log files."""
        script = textwrap.dedent("""\
            import json, sys
            print("warning on stderr", file=sys.stderr, flush=True)
            print(json.dumps({"kind": "assistant", "text": "done"}), flush=True)
        """)
        backend = FakeBackend(script)
        prompts = {'stderr-test': 'test prompt'}

        launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        stderr_content = (tmp_path / 'stderr-test.stderr.log').read_text()
        assert 'warning on stderr' in stderr_content

    def test_max_workers_capped(self, tmp_path: Path):
        """Workers are capped at min(len(prompts), MULTI_AGENT_MAX_WORKERS)."""
        script = _make_event_script({'kind': 'assistant', 'text': 'ok'})
        backend = FakeBackend(script)
        # Create more prompts than max workers
        prompts = {f'agent-{i}': f'prompt {i}' for i in range(8)}

        with patch('multi_agent.parallel.MULTI_AGENT_MAX_WORKERS', 3):
            results = launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        # All agents still complete, just with capped concurrency
        assert len(results) == 8
        for result in results.values():
            assert result.exit_code == 0

    def test_resources_initialized_to_none(self, tmp_path: Path):
        """Resources are initialized to None before try block for safe cleanup."""
        # This is a structural test — verify the pattern by causing an error
        # after Popen but the function still returns without fd leaks.

        class FailAfterPopenBackend(FakeBackend):
            def parse_events(self, lines):
                raise RuntimeError('simulated parse failure')

        script = _make_event_script({'kind': 'assistant', 'text': 'ok'})
        backend = FailAfterPopenBackend(script)
        prompts = {'fail-agent': 'test'}

        # Should not raise — the exception is caught and produces 'crashed'
        results = launch_parallel_agents(prompts, backend=backend, log_dir=tmp_path, timeout=30)

        assert results['fail-agent'].completion_status == 'crashed'

    def test_json_schema_passed_to_build_command(self, tmp_path: Path):
        """json_schema kwarg is forwarded to backend.build_command."""
        received_schemas: list = []

        class SchemaTrackingBackend(FakeBackend):
            def build_command(self, prompt, *, system_prompt='', max_turns=None, json_schema=None):
                received_schemas.append(json_schema)
                return super().build_command(prompt, system_prompt=system_prompt, max_turns=max_turns)

        schema = {'type': 'object', 'properties': {'winner': {'type': 'string'}}}
        script = _make_event_script({'kind': 'assistant', 'text': 'ok'})
        backend = SchemaTrackingBackend(script)

        launch_parallel_agents(
            {'A': 'prompt A'},
            backend=backend,
            log_dir=tmp_path,
            timeout=30,
            json_schema=schema,
        )

        assert received_schemas == [schema]

    def test_json_schema_none_by_default(self, tmp_path: Path):
        """json_schema defaults to None when not provided."""
        received_schemas: list = []

        class SchemaTrackingBackend(FakeBackend):
            def build_command(self, prompt, *, system_prompt='', max_turns=None, json_schema=None):
                received_schemas.append(json_schema)
                return super().build_command(prompt, system_prompt=system_prompt, max_turns=max_turns)

        script = _make_event_script({'kind': 'assistant', 'text': 'ok'})
        backend = SchemaTrackingBackend(script)

        launch_parallel_agents(
            {'A': 'prompt A'},
            backend=backend,
            log_dir=tmp_path,
            timeout=30,
        )

        assert received_schemas == [None]


# ===========================================================================
# Tracer integration
# ===========================================================================


class TestTracerIntegration:
    def test_tracer_emits_agent_spans(self, tmp_path: Path):
        """When a tracer is passed, begin/end spans are emitted per agent."""
        from multi_agent.trace import TraceWriter

        script = _make_event_script(
            {'kind': 'assistant', 'text': 'hello'},
            {'kind': 'result', 'text': 'done'},
        )
        backend = FakeBackend(script)
        prompts = {'A': 'do A', 'B': 'do B'}
        trace_path = tmp_path / 'trace.jsonl'
        tracer = TraceWriter(trace_path)

        results = launch_parallel_agents(
            prompts,
            backend=backend,
            log_dir=tmp_path,
            timeout=30,
            tracer=tracer,
            trace_parent_id='test-phase',
        )

        assert set(results.keys()) == {'A', 'B'}

        import json

        lines = trace_path.read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        # Should have begin + end for each agent (4 records minimum)
        begins = [r for r in records if r['event'] == 'begin']
        ends = [r for r in records if r['event'] == 'end']
        assert len(begins) >= 2
        assert len(ends) >= 2
        # All agent spans reference the parent and include log_path
        for b in begins:
            assert b['parent_id'] == 'test-phase'
            assert b['kind'] == 'agent'
            assert 'log_path' in b['details']
            assert b['details']['log_path'].endswith('.jsonl')

    def test_tracer_records_structured_output(self, tmp_path: Path):
        """Agent span end details include structured_output when present."""
        from multi_agent.trace import TraceWriter

        structured = {'winner': 'A', 'reason': 'simplicity'}
        script = _make_event_script(
            {'kind': 'assistant', 'text': 'I vote A'},
            {'kind': 'result', 'text': 'done', 'structured_output': structured},
        )

        class StructuredBackend(FakeBackend):
            def extract_result(self, events, exit_code):
                result = super().extract_result(events, exit_code)
                # Simulate reading structured_output from result event
                for ev in events:
                    if ev.kind == 'result':
                        result.structured_output = ev.raw.get('structured_output')
                return result

        backend = StructuredBackend(script)
        trace_path = tmp_path / 'trace.jsonl'
        tracer = TraceWriter(trace_path)

        results = launch_parallel_agents(
            {'A': 'vote'},
            backend=backend,
            log_dir=tmp_path,
            timeout=30,
            tracer=tracer,
            trace_parent_id='vote-phase',
        )

        assert results['A'].structured_output == structured

        lines = trace_path.read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        ends = [r for r in records if r['event'] == 'end' and r['kind'] == 'agent']
        assert len(ends) == 1
        assert ends[0]['details']['structured_output'] == structured

    def test_tracer_omits_structured_output_when_none(self, tmp_path: Path):
        """Agent span end details omit structured_output when None (no clutter)."""
        from multi_agent.trace import TraceWriter

        script = _make_event_script(
            {'kind': 'assistant', 'text': 'hello'},
            {'kind': 'result', 'text': 'done'},
        )
        backend = FakeBackend(script)
        trace_path = tmp_path / 'trace.jsonl'
        tracer = TraceWriter(trace_path)

        launch_parallel_agents(
            {'A': 'task'},
            backend=backend,
            log_dir=tmp_path,
            timeout=30,
            tracer=tracer,
            trace_parent_id='phase',
        )

        lines = trace_path.read_text().strip().splitlines()
        records = [json.loads(line) for line in lines]
        ends = [r for r in records if r['event'] == 'end' and r['kind'] == 'agent']
        assert len(ends) == 1
        assert 'structured_output' not in ends[0]['details']

    def test_no_tracer_no_trace_file(self, tmp_path: Path):
        """When tracer is None, no trace file is created."""
        script = _make_event_script(
            {'kind': 'assistant', 'text': 'hello'},
        )
        backend = FakeBackend(script)
        prompts = {'A': 'do A'}

        launch_parallel_agents(
            prompts,
            backend=backend,
            log_dir=tmp_path,
            timeout=30,
        )

        assert not (tmp_path / 'trace.jsonl').exists()
