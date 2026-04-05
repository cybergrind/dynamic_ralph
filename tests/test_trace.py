"""Tests for multi_agent.trace — TraceSpan, TraceWriter, format_trace_report."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from multi_agent.trace import TraceSpan, TraceWriter, TracingContext, format_agent_log, format_trace_report


# ===========================================================================
# TracingContext
# ===========================================================================


class TestTracingContext:
    def test_begin_returns_none_without_writer(self):
        ctx = TracingContext()
        assert ctx.begin('span-1', 'agent', 'agent-A') is None

    def test_end_noop_without_writer(self):
        ctx = TracingContext()
        ctx.end(None)  # should not raise

    def test_begin_delegates_to_writer(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        ctx = TracingContext(writer=writer, parent_span_id='run')
        span = ctx.begin('round-1', 'round', 'round-1')
        assert span is not None
        assert span.parent_id == 'run'

    def test_child_inherits_writer_and_identity(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        ctx = TracingContext(writer=writer, parent_span_id='run', identity_names={'A': 'i_consul.md'})
        child = ctx.child('round-1')
        assert child.writer is writer
        assert child.parent_span_id == 'round-1'
        assert child.identity_names == {'A': 'i_consul.md'}

    def test_progress_noop_without_writer(self):
        ctx = TracingContext()
        ctx.progress(None, 'hello')  # should not raise


# ===========================================================================
# TraceWriter basics
# ===========================================================================


class TestTraceWriterBeginEnd:
    def test_begin_writes_jsonl_line(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        writer.begin('run-1', 'run', 'run')
        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record['event'] == 'begin'
        assert record['span_id'] == 'run-1'
        assert record['kind'] == 'run'
        assert record['label'] == 'run'
        assert 'started_at' in record
        assert 't_offset_secs' in record
        assert isinstance(record['t_offset_secs'], float)

    def test_end_writes_jsonl_line_with_elapsed(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        span = writer.begin('run-1', 'run', 'run')
        writer.end(span)
        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        assert len(lines) == 2
        record = json.loads(lines[1])
        assert record['event'] == 'end'
        assert record['span_id'] == 'run-1'
        assert 'ended_at' in record
        assert 'elapsed_secs' in record
        assert isinstance(record['elapsed_secs'], float)
        assert record['elapsed_secs'] >= 0

    def test_end_updates_span_fields(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        span = writer.begin('run-1', 'run', 'run')
        assert span.ended_at is None
        assert span.elapsed_secs is None
        writer.end(span)
        assert span.ended_at is not None
        assert span.elapsed_secs is not None

    def test_begin_with_parent_id(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        writer.begin('run-1', 'run', 'run')
        child = writer.begin('round-1', 'round', 'round-1', parent_id='run-1')
        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        record = json.loads(lines[1])
        assert record['parent_id'] == 'run-1'
        assert child.parent_id == 'run-1'

    def test_begin_with_details(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        writer.begin('agent-A', 'agent', 'agent-A', cost_usd=0.05)
        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        record = json.loads(lines[0])
        assert record['details']['cost_usd'] == 0.05

    def test_end_with_details(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        span = writer.begin('agent-A', 'agent', 'agent-A')
        writer.end(span, cost_usd=0.12, timed_out=False)
        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        record = json.loads(lines[1])
        assert record['details']['cost_usd'] == 0.12
        assert record['details']['timed_out'] is False


class TestTraceWriterProgress:
    def test_progress_writes_jsonl_line(self, tmp_path):
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        span = writer.begin('agent-A', 'agent', 'agent-A')
        writer.progress(span, 'still running, 10 events received')
        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        assert len(lines) == 2
        record = json.loads(lines[1])
        assert record['event'] == 'progress'
        assert record['span_id'] == 'agent-A'
        assert record['message'] == 'still running, 10 events received'
        assert 't_offset_secs' in record


class TestTraceWriterThreadSafety:
    def test_concurrent_writes_no_corruption(self, tmp_path):
        """Multiple threads writing begin/end spans concurrently."""
        writer = TraceWriter(tmp_path / 'trace.jsonl')
        num_threads = 10
        spans_per_thread = 20
        barrier = threading.Barrier(num_threads)

        def worker(thread_id):
            barrier.wait()
            for i in range(spans_per_thread):
                span = writer.begin(f't{thread_id}-s{i}', 'agent', f'agent-{thread_id}-{i}')
                writer.end(span)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        lines = (tmp_path / 'trace.jsonl').read_text().strip().splitlines()
        # Each span produces 2 lines (begin + end)
        assert len(lines) == num_threads * spans_per_thread * 2
        # Every line must be valid JSON
        for line in lines:
            json.loads(line)


class TestLoadAgentSpans:
    def _write_trace(self, path: Path, events: list[dict]):
        with open(path, 'w') as f:
            for ev in events:
                f.write(json.dumps(ev) + '\n')

    def test_loads_structured_output_from_end_details(self, tmp_path):
        """structured_output in agent end details is captured in AgentSpanInfo."""
        from multi_agent.trace import load_agent_spans

        structured = {'winner': 'A', 'decisive_argument': 'simplicity'}
        trace_path = tmp_path / 'trace.jsonl'
        self._write_trace(
            trace_path,
            [
                {
                    'event': 'begin',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'started_at': '2026-04-05T10:00:00+00:00',
                    't_offset_secs': 0.0,
                    'parent_id': 'vote',
                    'details': {'log_path': '/logs/vote-A.jsonl'},
                },
                {
                    'event': 'end',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'ended_at': '2026-04-05T10:00:05+00:00',
                    't_offset_secs': 5.0,
                    'elapsed_secs': 5.0,
                    'details': {
                        'cost_usd': 0.01,
                        'timed_out': False,
                        'structured_output': structured,
                    },
                },
            ],
        )

        spans = load_agent_spans(trace_path)
        assert len(spans) == 1
        assert spans[0].structured_output == structured

    def test_identity_loaded_from_begin_details(self, tmp_path):
        """identity in agent begin details is captured in AgentSpanInfo."""
        from multi_agent.trace import load_agent_spans

        trace_path = tmp_path / 'trace.jsonl'
        self._write_trace(
            trace_path,
            [
                {
                    'event': 'begin',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'started_at': '2026-04-05T10:00:00+00:00',
                    't_offset_secs': 0.0,
                    'parent_id': 'propose',
                    'details': {'log_path': '/logs/A.jsonl', 'identity': 'i_consul.md'},
                },
                {
                    'event': 'end',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'ended_at': '2026-04-05T10:00:05+00:00',
                    't_offset_secs': 5.0,
                    'elapsed_secs': 5.0,
                    'details': {'cost_usd': 0.05, 'timed_out': False},
                },
            ],
        )

        spans = load_agent_spans(trace_path)
        assert len(spans) == 1
        assert spans[0].identity == 'i_consul.md'

    def test_identity_none_when_absent(self, tmp_path):
        """identity defaults to None when not in begin details (backward compat)."""
        from multi_agent.trace import load_agent_spans

        trace_path = tmp_path / 'trace.jsonl'
        self._write_trace(
            trace_path,
            [
                {
                    'event': 'begin',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'started_at': '2026-04-05T10:00:00+00:00',
                    't_offset_secs': 0.0,
                    'parent_id': 'propose',
                    'details': {'log_path': '/logs/A.jsonl'},
                },
                {
                    'event': 'end',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'ended_at': '2026-04-05T10:00:05+00:00',
                    't_offset_secs': 5.0,
                    'elapsed_secs': 5.0,
                    'details': {'cost_usd': 0.05, 'timed_out': False},
                },
            ],
        )

        spans = load_agent_spans(trace_path)
        assert len(spans) == 1
        assert spans[0].identity is None

    def test_structured_output_none_when_absent(self, tmp_path):
        """structured_output defaults to None when not in trace details."""
        from multi_agent.trace import load_agent_spans

        trace_path = tmp_path / 'trace.jsonl'
        self._write_trace(
            trace_path,
            [
                {
                    'event': 'begin',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'started_at': '2026-04-05T10:00:00+00:00',
                    't_offset_secs': 0.0,
                    'parent_id': 'propose',
                    'details': {'log_path': '/logs/propose-A.jsonl'},
                },
                {
                    'event': 'end',
                    'span_id': 'agent-A',
                    'kind': 'agent',
                    'label': 'agent-A',
                    'ended_at': '2026-04-05T10:00:05+00:00',
                    't_offset_secs': 5.0,
                    'elapsed_secs': 5.0,
                    'details': {'cost_usd': 0.05, 'timed_out': False},
                },
            ],
        )

        spans = load_agent_spans(trace_path)
        assert len(spans) == 1
        assert spans[0].structured_output is None


class TestFormatAgentLogBaseDir:
    def test_relative_log_path_resolved_against_base_dir(self, tmp_path):
        """format_agent_log resolves relative paths against base_dir."""
        from multi_agent.trace import format_agent_log

        # Create log file in a subdirectory
        logs_dir = tmp_path / 'run_dir' / 'logs'
        logs_dir.mkdir(parents=True)
        log_file = logs_dir / 'vote-A.jsonl'
        log_file.write_text(json.dumps({'type': 'result', 'subtype': 'end_turn', 'num_turns': 1}) + '\n')

        # Relative path as stored in trace.jsonl
        relative_path = Path('logs/vote-A.jsonl')
        base_dir = tmp_path / 'run_dir'

        result = format_agent_log(relative_path, base_dir=base_dir)
        assert result != '(log file not found)'
        assert result != '(no events)'
        assert 'end_turn' in result

    def test_relative_path_includes_run_dir_prefix(self, tmp_path):
        """log_path that already includes the run dir prefix still resolves.

        In practice, log_path is stored as the full relative path from CWD at
        recording time (e.g. run_ralph/multi-agent/<id>/logs/propose-A.jsonl).
        base_dir is the absolute run directory. Naive base_dir/log_path would
        double the path. The function must try the path as-is first.
        """
        from multi_agent.trace import format_agent_log

        # Simulate: run dir at tmp_path/run_ralph/multi-agent/run123
        run_dir = tmp_path / 'run_ralph' / 'multi-agent' / 'run123'
        logs_dir = run_dir / 'logs'
        logs_dir.mkdir(parents=True)
        log_file = logs_dir / 'propose-A.jsonl'
        log_file.write_text(
            json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'hello'}]}}) + '\n'
        )

        # CWD is tmp_path — log_path is relative from there
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            # log_path as stored: full relative from CWD
            log_path = Path('run_ralph/multi-agent/run123/logs/propose-A.jsonl')
            # base_dir is the absolute run directory
            base_dir = run_dir

            result = format_agent_log(log_path, base_dir=base_dir)
            assert result != '(log file not found)', f'Could not find {log_path} with base_dir={base_dir}'
            assert 'hello' in result
        finally:
            os.chdir(old_cwd)

    def test_absolute_log_path_ignores_base_dir(self, tmp_path):
        """Absolute paths work regardless of base_dir."""
        from multi_agent.trace import format_agent_log

        log_file = tmp_path / 'vote-A.jsonl'
        log_file.write_text(json.dumps({'type': 'result', 'subtype': 'end_turn', 'num_turns': 1}) + '\n')

        result = format_agent_log(log_file, base_dir=Path('/nonexistent'))
        assert 'end_turn' in result

    def test_no_base_dir_uses_path_as_is(self, tmp_path):
        """Without base_dir, path is used directly (backward compat)."""
        from multi_agent.trace import format_agent_log

        log_file = tmp_path / 'vote-A.jsonl'
        log_file.write_text(json.dumps({'type': 'result', 'subtype': 'end_turn', 'num_turns': 1}) + '\n')

        result = format_agent_log(log_file)
        assert 'end_turn' in result


class TestTraceSpan:
    def test_span_fields(self):
        span = TraceSpan(
            span_id='test-1',
            kind='run',
            label='test',
            started_at='2026-04-05T10:00:00+00:00',
            ended_at=None,
            elapsed_secs=None,
            parent_id=None,
            details={},
        )
        assert span.span_id == 'test-1'
        assert span.ended_at is None


# ===========================================================================
# format_trace_report
# ===========================================================================


class TestFormatTraceReport:
    def _write_trace(self, path: Path, events: list[dict]):
        with open(path, 'w') as f:
            for event in events:
                f.write(json.dumps(event) + '\n')

    def test_basic_report_structure(self, tmp_path):
        """A single round with propose/debate/vote produces readable output."""
        trace_path = tmp_path / 'trace.jsonl'
        events = [
            {
                'event': 'begin',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': None,
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'round-1',
                'kind': 'round',
                'label': 'round-1',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'run',
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'round-1-propose',
                'kind': 'phase',
                'label': 'propose',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'round-1',
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'round-1-propose-A',
                'kind': 'agent',
                'label': 'agent-A',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'round-1-propose',
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'round-1-propose-A',
                'kind': 'agent',
                'label': 'agent-A',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {'cost_usd': 0.05, 'timed_out': False},
            },
            {
                'event': 'end',
                'span_id': 'round-1-propose',
                'kind': 'phase',
                'label': 'propose',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'round-1',
                'kind': 'round',
                'label': 'round-1',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
        ]
        self._write_trace(trace_path, events)
        report = format_trace_report(trace_path)
        assert 'round-1' in report.lower() or 'Round 1' in report
        assert 'propose' in report.lower()
        assert 'agent-A' in report or 'agent-a' in report.lower()
        assert '30.0s' in report or '30.0' in report

    def test_empty_trace_file(self, tmp_path):
        trace_path = tmp_path / 'trace.jsonl'
        trace_path.write_text('')
        report = format_trace_report(trace_path)
        assert 'no trace data' in report.lower() or report.strip() == ''

    def test_question_not_in_report(self, tmp_path):
        """Question is shown in TUI header, not duplicated in the report body."""
        trace_path = tmp_path / 'trace.jsonl'
        events = [
            {
                'event': 'begin',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': None,
                'details': {'question': 'How should we refactor the auth module?'},
            },
            {
                'event': 'end',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
        ]
        self._write_trace(trace_path, events)
        report = format_trace_report(trace_path)
        assert 'Question:' not in report

    def test_identity_shown_in_agent_line(self, tmp_path):
        """Identity name appears in agent line of formatted report."""
        trace_path = tmp_path / 'trace.jsonl'
        events = [
            {
                'event': 'begin',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': None,
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'r1-propose',
                'kind': 'phase',
                'label': 'propose',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'run',
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'r1-propose-A',
                'kind': 'agent',
                'label': 'agent-A',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'r1-propose',
                'details': {'identity': 'i_consul.md'},
            },
            {
                'event': 'end',
                'span_id': 'r1-propose-A',
                'kind': 'agent',
                'label': 'agent-A',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {'cost_usd': 0.05, 'timed_out': False},
            },
            {
                'event': 'end',
                'span_id': 'r1-propose',
                'kind': 'phase',
                'label': 'propose',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
        ]
        self._write_trace(trace_path, events)
        report = format_trace_report(trace_path)
        assert '(i_consul.md)' in report

    def test_question_omitted_when_absent(self, tmp_path):
        """Backward compat: no question in details → no Question line."""
        trace_path = tmp_path / 'trace.jsonl'
        events = [
            {
                'event': 'begin',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': None,
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'ended_at': '2026-04-05T10:00:30+00:00',
                't_offset_secs': 30.0,
                'elapsed_secs': 30.0,
                'details': {},
            },
        ]
        self._write_trace(trace_path, events)
        report = format_trace_report(trace_path)
        assert 'Question:' not in report

    def test_agent_details_shown(self, tmp_path):
        """Agent cost and timed_out status appear in the report."""
        trace_path = tmp_path / 'trace.jsonl'
        events = [
            {
                'event': 'begin',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': None,
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'r1-propose',
                'kind': 'phase',
                'label': 'propose',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'run',
                'details': {},
            },
            {
                'event': 'begin',
                'span_id': 'r1-propose-B',
                'kind': 'agent',
                'label': 'agent-B',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'r1-propose',
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'r1-propose-B',
                'kind': 'agent',
                'label': 'agent-B',
                'ended_at': '2026-04-05T10:01:00+00:00',
                't_offset_secs': 60.0,
                'elapsed_secs': 60.0,
                'details': {'cost_usd': 0.15, 'timed_out': True},
            },
            {
                'event': 'end',
                'span_id': 'r1-propose',
                'kind': 'phase',
                'label': 'propose',
                'ended_at': '2026-04-05T10:01:00+00:00',
                't_offset_secs': 60.0,
                'elapsed_secs': 60.0,
                'details': {},
            },
            {
                'event': 'end',
                'span_id': 'run',
                'kind': 'run',
                'label': 'run',
                'ended_at': '2026-04-05T10:01:00+00:00',
                't_offset_secs': 60.0,
                'elapsed_secs': 60.0,
                'details': {},
            },
        ]
        self._write_trace(trace_path, events)
        report = format_trace_report(trace_path)
        assert '$0.15' in report or '0.15' in report
        assert 'TIMEOUT' in report or 'timed_out' in report.lower() or 'timeout' in report.lower()


# ===========================================================================
# format_agent_log
# ===========================================================================


class TestFormatAgentLog:
    def _write_log(self, path: Path, events: list[dict]):
        with open(path, 'w') as f:
            for ev in events:
                f.write(json.dumps(ev) + '\n')

    def test_shows_all_event_types(self, tmp_path):
        log_path = tmp_path / 'agent-A.jsonl'
        self._write_log(
            log_path,
            [
                {'type': 'system', 'model': 'claude-opus-4-6'},
                {
                    'type': 'assistant',
                    'message': {'content': [{'type': 'text', 'text': 'I will fix the bug.'}]},
                },
                {
                    'type': 'assistant',
                    'message': {
                        'content': [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': '/src/main.py'}}]
                    },
                },
                {
                    'type': 'user',
                    'tool_use_result': {'stdout': 'def main():\n    pass', 'stderr': ''},
                },
                {'type': 'result', 'subtype': 'end_turn', 'num_turns': 3, 'total_cost_usd': 0.05},
            ],
        )
        output = format_agent_log(log_path)
        assert 'system' in output.lower() or 'claude-opus' in output
        assert 'I will fix the bug' in output
        assert 'Read' in output
        assert 'main()' in output
        assert 'end_turn' in output or 'result' in output.lower()

    def test_nonexistent_file(self, tmp_path):
        output = format_agent_log(tmp_path / 'missing.jsonl')
        assert 'not found' in output.lower() or output.strip() == ''

    def test_empty_file(self, tmp_path):
        log_path = tmp_path / 'empty.jsonl'
        log_path.write_text('')
        output = format_agent_log(log_path)
        assert 'no events' in output.lower() or 'empty' in output.lower() or output.strip() == ''
