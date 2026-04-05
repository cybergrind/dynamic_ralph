"""Trace viewer TUI — dual-pane browser for multi-agent run traces.

Left pane: list of recent runs (newest first).
Right pane: trace report for the selected run (scrolled to bottom).

Navigation: j/k up/down, h/l switch pane, q quit.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from multi_agent.trace import format_trace_report


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------


@dataclass
class RunEntry:
    """Summary of a discovered run directory."""

    run_id: str
    path: Path
    status: str
    question: str
    trace_path: Path | None


def _discover_runs(base_dir: Path) -> list[RunEntry]:
    """Scan *base_dir* for run directories, return sorted newest-first."""
    entries: list[RunEntry] = []
    if not base_dir.is_dir():
        return entries

    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        meta_path = child / 'metadata.json'
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        trace_path = child / 'trace.jsonl'
        entries.append(
            RunEntry(
                run_id=meta.get('run_id', child.name),
                path=child,
                status=meta.get('status', 'unknown'),
                question=meta.get('question', ''),
                trace_path=trace_path if trace_path.exists() else None,
            )
        )

    # Sort newest first (run_id starts with timestamp)
    entries.sort(key=lambda e: e.run_id, reverse=True)
    return entries


# ---------------------------------------------------------------------------
# Textual TUI
# ---------------------------------------------------------------------------


def _build_app():
    """Build and return the Textual App class (import deferred for testability)."""
    from typing import ClassVar

    from textual.app import App, ComposeResult
    from textual.binding import Binding, BindingType
    from textual.containers import Horizontal
    from textual.widgets import Footer, Header, OptionList, Static
    from textual.widgets.option_list import Option

    class TraceApp(App):
        CSS: ClassVar[str] = """
        #left-pane {
            width: 1fr;
            min-width: 30;
            border: solid $primary;
        }
        #right-pane {
            width: 3fr;
            border: solid $secondary;
            overflow-y: auto;
        }
        #left-pane.--focused {
            border: heavy $primary;
        }
        #right-pane.--focused {
            border: heavy $secondary;
        }
        #trace-content {
            width: 100%;
        }
        """

        BINDINGS: ClassVar[list[BindingType]] = [
            Binding('q', 'quit', 'Quit'),
            Binding('h', 'focus_left', 'Left pane', show=False),
            Binding('l', 'focus_right', 'Right pane', show=False),
        ]

        def __init__(self, base_dir: Path) -> None:
            super().__init__()
            self.base_dir = base_dir
            self.runs: list[RunEntry] = []
            self._right_pane_focused = False

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield OptionList(id='left-pane')
                yield Static('(select a run)', id='right-pane')
            yield Footer()

        def on_mount(self) -> None:
            self.runs = _discover_runs(self.base_dir)
            option_list = self.query_one('#left-pane', OptionList)
            for run in self.runs:
                status_icon = '✓' if run.status == 'completed' else '✗' if run.status == 'failed' else '…'
                label = f'{status_icon} {run.run_id[:20]}'
                option_list.add_option(Option(label, id=run.run_id))

            if self.runs:
                option_list.highlighted = 0
                self._show_trace(0)

            option_list.focus()

        def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
            if event.option and event.option.id:
                idx = next((i for i, r in enumerate(self.runs) if r.run_id == event.option.id), None)
                if idx is not None:
                    self._show_trace(idx)

        def _show_trace(self, idx: int) -> None:
            run = self.runs[idx]
            right = self.query_one('#right-pane', Static)
            if run.trace_path:
                report = format_trace_report(run.trace_path)
            else:
                report = '(no trace data)'
            header = f'Run: {run.run_id}\nStatus: {run.status}\nQuestion: {run.question}\n\n'
            right.update(header + report)
            # Scroll to bottom
            right.scroll_end(animate=False)

        def action_focus_left(self) -> None:
            self._right_pane_focused = False
            self.query_one('#left-pane', OptionList).focus()

        def action_focus_right(self) -> None:
            self._right_pane_focused = True
            self.query_one('#right-pane', Static).focus()

    return TraceApp


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description='Trace viewer for multi-agent runs')
    parser.add_argument(
        '--dir',
        type=Path,
        default=Path('run_ralph') / 'multi-agent',
        help='Base directory for run artifacts (default: run_ralph/multi-agent)',
    )
    args = parser.parse_args()

    if not args.dir.is_dir():
        print(f'Directory not found: {args.dir}', file=sys.stderr)
        sys.exit(1)

    TraceApp = _build_app()
    app = TraceApp(args.dir)
    app.run()


if __name__ == '__main__':
    main()
