"""Lightweight tracing for multi-agent orchestration runs.

Records timestamped spans to a ``trace.jsonl`` file for post-hoc analysis.
Thread-safe — designed for use inside ``ThreadPoolExecutor`` in ``parallel.py``.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TraceSpan:
    """A single trace span representing a unit of work."""

    span_id: str
    kind: str  # "run" | "round" | "phase" | "agent" | "quorum_retry" | "extract_retry"
    label: str
    started_at: str  # ISO 8601
    ended_at: str | None = None
    elapsed_secs: float | None = None
    parent_id: str | None = None
    details: dict = field(default_factory=dict)

    # Internal: monotonic timestamp for elapsed calculation
    _mono_start: float = field(default=0.0, repr=False)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class TraceWriter:
    """Thread-safe, append-only JSONL trace writer."""

    def __init__(self, trace_path: Path) -> None:
        self._path = trace_path
        self._lock = threading.Lock()
        self._t0 = time.monotonic()

    def begin(
        self,
        span_id: str,
        kind: str,
        label: str,
        parent_id: str | None = None,
        **details: object,
    ) -> TraceSpan:
        """Record the start of a span. Returns the span for later ``end()``."""
        now_mono = time.monotonic()
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        span = TraceSpan(
            span_id=span_id,
            kind=kind,
            label=label,
            started_at=now_iso,
            parent_id=parent_id,
            details=dict(details) if details else {},
            _mono_start=now_mono,
        )
        self._write(
            {
                'event': 'begin',
                'span_id': span_id,
                'kind': kind,
                'label': label,
                'started_at': now_iso,
                't_offset_secs': round(now_mono - self._t0, 3),
                'parent_id': parent_id,
                'details': span.details,
            }
        )
        return span

    def end(self, span: TraceSpan, **details: object) -> None:
        """Record the end of a span. Updates *span* in-place."""
        now_mono = time.monotonic()
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        elapsed = now_mono - span._mono_start
        span.ended_at = now_iso
        span.elapsed_secs = round(elapsed, 3)
        if details:
            span.details.update(details)
        self._write(
            {
                'event': 'end',
                'span_id': span.span_id,
                'kind': span.kind,
                'label': span.label,
                'ended_at': now_iso,
                't_offset_secs': round(now_mono - self._t0, 3),
                'elapsed_secs': span.elapsed_secs,
                'details': span.details,
            }
        )

    def progress(self, span: TraceSpan, message: str) -> None:
        """Record a progress heartbeat for an in-flight span."""
        now_mono = time.monotonic()
        self._write(
            {
                'event': 'progress',
                'span_id': span.span_id,
                'kind': span.kind,
                'label': span.label,
                't_offset_secs': round(now_mono - self._t0, 3),
                'message': message,
            }
        )

    def _write(self, record: dict) -> None:
        line = json.dumps(record) + '\n'
        with self._lock:
            with open(self._path, 'a') as f:
                f.write(line)


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------


def format_trace_report(trace_path: Path) -> str:
    """Read trace.jsonl and produce a human-readable timeline report."""
    if not trace_path.exists():
        return '(no trace data)'

    text = trace_path.read_text().strip()
    if not text:
        return '(no trace data)'

    events = [json.loads(line) for line in text.splitlines()]

    # Index end events by span_id for easy lookup
    end_events: dict[str, dict] = {}
    begin_events: dict[str, dict] = {}
    for ev in events:
        if ev['event'] == 'end':
            end_events[ev['span_id']] = ev
        elif ev['event'] == 'begin':
            begin_events[ev['span_id']] = ev

    # Build hierarchy: find run, rounds, phases, agents
    lines: list[str] = []

    # Run header
    run_end = end_events.get('run')
    if run_end:
        elapsed = run_end.get('elapsed_secs', '?')
        lines.append(f'Total: {elapsed}s')
        lines.append('')

    # Group spans by parent
    children: dict[str | None, list[str]] = {}
    for span_id, ev in begin_events.items():
        parent = ev.get('parent_id')
        children.setdefault(parent, []).append(span_id)

    def _format_agent(span_id: str) -> str:
        begin = begin_events.get(span_id, {})
        end = end_events.get(span_id)
        label = begin.get('label', span_id)
        if end:
            elapsed = end.get('elapsed_secs', '?')
            details = end.get('details', {})
            cost = details.get('cost_usd')
            timed_out = details.get('timed_out', False)
            parts = [f'    {label}  {elapsed}s']
            if cost is not None:
                parts.append(f'${cost:.2f}')
            if timed_out:
                parts.append('TIMEOUT')
            else:
                parts.append('ok')
            return '  '.join(parts)
        else:
            return f'    {label}  (in progress)'

    def _format_phase(span_id: str) -> list[str]:
        begin = begin_events.get(span_id, {})
        end = end_events.get(span_id)
        label = begin.get('label', span_id).upper()
        if end:
            elapsed = end.get('elapsed_secs', '?')
            result = [f'  {label} ({elapsed}s)']
        else:
            result = [f'  {label} (in progress)']
        # Agent children
        for child_id in children.get(span_id, []):
            child_begin = begin_events.get(child_id, {})
            if child_begin.get('kind') == 'agent':
                result.append(_format_agent(child_id))
        return result

    def _format_round(span_id: str) -> list[str]:
        begin = begin_events.get(span_id, {})
        end = end_events.get(span_id)
        label = begin.get('label', span_id)
        if end:
            elapsed = end.get('elapsed_secs', '?')
            result = [f'{label} ({elapsed}s)']
        else:
            result = [f'{label} (in progress)']
        # Phase children
        for child_id in children.get(span_id, []):
            child_begin = begin_events.get(child_id, {})
            if child_begin.get('kind') == 'phase':
                result.extend(_format_phase(child_id))
        return result

    # Format rounds under run
    for round_id in children.get('run', []):
        round_begin = begin_events.get(round_id, {})
        if round_begin.get('kind') == 'round':
            lines.extend(_format_round(round_id))
            lines.append('')

    # Also handle phases directly under run (no round wrapper)
    for child_id in children.get('run', []):
        child_begin = begin_events.get(child_id, {})
        if child_begin.get('kind') == 'phase':
            lines.extend(_format_phase(child_id))

    return '\n'.join(lines).rstrip()
