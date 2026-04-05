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
