"""Tests for multi_agent.trace — TraceSpan, TraceWriter, format_trace_report."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from multi_agent.trace import TraceSpan, TraceWriter, format_trace_report


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
