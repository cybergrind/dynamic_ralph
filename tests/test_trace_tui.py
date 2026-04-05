"""Tests for bin.run_trace — trace TUI."""

from __future__ import annotations

import json
from pathlib import Path


# ===========================================================================
# _discover_runs
# ===========================================================================


class TestDiscoverRuns:
    def _make_run(self, base: Path, run_id: str, status: str = 'completed', elapsed: float = 30.0):
        run_dir = base / run_id
        run_dir.mkdir(parents=True)
        meta = {
            'run_id': run_id,
            'status': status,
            'started_at': '2026-04-05T10:00:00+00:00',
            'finished_at': '2026-04-05T10:00:30+00:00',
            'question': 'Test question',
        }
        (run_dir / 'metadata.json').write_text(json.dumps(meta))
        # Write a minimal trace
        trace_events = [
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
                't_offset_secs': elapsed,
                'elapsed_secs': elapsed,
                'details': {'status': status},
            },
        ]
        with open(run_dir / 'trace.jsonl', 'w') as f:
            for ev in trace_events:
                f.write(json.dumps(ev) + '\n')

    def test_discovers_runs_sorted_newest_first(self, tmp_path: Path):
        from bin.run_trace import _discover_runs

        self._make_run(tmp_path, '20260405T100000_aaaa0001')
        self._make_run(tmp_path, '20260405T110000_bbbb0002')
        self._make_run(tmp_path, '20260405T090000_cccc0003')

        runs = _discover_runs(tmp_path)
        assert len(runs) == 3
        # Newest first
        assert runs[0].run_id == '20260405T110000_bbbb0002'
        assert runs[1].run_id == '20260405T100000_aaaa0001'
        assert runs[2].run_id == '20260405T090000_cccc0003'

    def test_empty_directory(self, tmp_path: Path):
        from bin.run_trace import _discover_runs

        runs = _discover_runs(tmp_path)
        assert runs == []

    def test_run_without_metadata_skipped(self, tmp_path: Path):
        from bin.run_trace import _discover_runs

        # Create a directory without metadata.json
        (tmp_path / 'some_dir').mkdir()
        runs = _discover_runs(tmp_path)
        assert runs == []

    def test_run_entry_has_status_and_question(self, tmp_path: Path):
        from bin.run_trace import _discover_runs

        self._make_run(tmp_path, '20260405T100000_aaaa0001', status='failed')
        runs = _discover_runs(tmp_path)
        assert len(runs) == 1
        assert runs[0].status == 'failed'
        assert runs[0].question == 'Test question'


# ===========================================================================
# build_line_agent_map
# ===========================================================================


def _make_two_round_trace(trace_path: Path) -> None:
    """Write a trace with 2 rounds, each containing agent-A with different log paths."""
    events: list[dict[str, object]] = []
    # Run begin
    events.append(
        {
            'event': 'begin',
            'span_id': 'run',
            'kind': 'run',
            'label': 'run',
            'started_at': '2026-01-01T00:00:00Z',
            't_offset_secs': 0.0,
            'parent_id': None,
            'details': {},
        }
    )
    for rnd in (1, 2):
        r_id = f'round-{rnd}'
        events.append(
            {
                'event': 'begin',
                'span_id': r_id,
                'kind': 'round',
                'label': r_id,
                'started_at': '2026-01-01T00:00:00Z',
                't_offset_secs': 0.0,
                'parent_id': 'run',
                'details': {},
            }
        )
        p_id = f'{r_id}-propose'
        events.append(
            {
                'event': 'begin',
                'span_id': p_id,
                'kind': 'phase',
                'label': 'propose',
                'started_at': '2026-01-01T00:00:00Z',
                't_offset_secs': 0.0,
                'parent_id': r_id,
                'details': {},
            }
        )
        a_id = f'{r_id}-agent-A'
        events.append(
            {
                'event': 'begin',
                'span_id': a_id,
                'kind': 'agent',
                'label': 'agent-A',
                'started_at': '2026-01-01T00:00:00Z',
                't_offset_secs': 0.0,
                'parent_id': p_id,
                'details': {'log_path': f'/logs/round{rnd}_agent-A.jsonl'},
            }
        )
        events.append(
            {
                'event': 'end',
                'span_id': a_id,
                'kind': 'agent',
                'label': 'agent-A',
                'ended_at': '2026-01-01T00:00:10Z',
                't_offset_secs': 10.0,
                'elapsed_secs': 10.0,
                'details': {'cost_usd': 0.05, 'timed_out': False},
            }
        )
        events.append(
            {
                'event': 'end',
                'span_id': p_id,
                'kind': 'phase',
                'label': 'propose',
                'ended_at': '2026-01-01T00:00:10Z',
                't_offset_secs': 10.0,
                'elapsed_secs': 10.0,
                'details': {},
            }
        )
        events.append(
            {
                'event': 'end',
                'span_id': r_id,
                'kind': 'round',
                'label': r_id,
                'ended_at': '2026-01-01T00:00:10Z',
                't_offset_secs': 10.0,
                'elapsed_secs': 10.0,
                'details': {},
            }
        )
    # Run end
    events.append(
        {
            'event': 'end',
            'span_id': 'run',
            'kind': 'run',
            'label': 'run',
            'ended_at': '2026-01-01T00:00:20Z',
            't_offset_secs': 20.0,
            'elapsed_secs': 20.0,
            'details': {},
        }
    )
    with open(trace_path, 'w') as f:
        for ev in events:
            f.write(json.dumps(ev) + '\n')


class TestAgentSpanStructuredOutput:
    def test_structured_output_available_for_tui(self, tmp_path: Path):
        """load_agent_spans carries structured_output for TUI introspection."""
        from multi_agent.trace import load_agent_spans

        structured = {'winner': 'A', 'decisive_argument': 'best approach'}
        trace_path = tmp_path / 'trace.jsonl'
        events = [
            {
                'event': 'begin',
                'span_id': 'vote-A',
                'kind': 'agent',
                'label': 'agent-A',
                'started_at': '2026-04-05T10:00:00+00:00',
                't_offset_secs': 0.0,
                'parent_id': 'vote',
                'details': {'log_path': 'logs/vote-A.jsonl'},
            },
            {
                'event': 'end',
                'span_id': 'vote-A',
                'kind': 'agent',
                'label': 'agent-A',
                'ended_at': '2026-04-05T10:00:03+00:00',
                't_offset_secs': 3.0,
                'elapsed_secs': 3.0,
                'details': {
                    'cost_usd': 0.01,
                    'timed_out': False,
                    'structured_output': structured,
                },
            },
        ]
        with open(trace_path, 'w') as f:
            for ev in events:
                f.write(json.dumps(ev) + '\n')

        spans = load_agent_spans(trace_path)
        assert len(spans) == 1
        assert spans[0].structured_output is not None
        assert spans[0].structured_output == structured
        assert spans[0].structured_output['winner'] == 'A'


class TestBuildLineAgentMap:
    def test_duplicate_labels_map_to_distinct_spans(self, tmp_path: Path):
        """agent-A in round-1 and round-2 must map to different spans."""
        from bin.run_trace import build_line_agent_map
        from multi_agent.trace import format_trace_report, load_agent_spans

        trace_path = tmp_path / 'trace.jsonl'
        _make_two_round_trace(trace_path)

        report = format_trace_report(trace_path)
        spans = load_agent_spans(trace_path)

        line_map = build_line_agent_map(report, spans)

        # Should have 2 mapped lines (one per round)
        mapped_spans = list(line_map.values())
        assert len(mapped_spans) == 2
        # They must be distinct span objects with different log paths
        assert mapped_spans[0].log_path != mapped_spans[1].log_path
        assert mapped_spans[0].log_path == '/logs/round1_agent-A.jsonl'
        assert mapped_spans[1].log_path == '/logs/round2_agent-A.jsonl'
