"""Trace viewer TUI — dual-pane browser for multi-agent run traces.

Left pane: list of recent runs (newest first).
Right pane: trace report for the selected run (scrolled to bottom).

Navigation: j/k up/down, h/l switch pane, q quit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from multi_agent.trace import AgentSpanInfo, format_agent_log, format_trace_report, load_agent_spans


# ---------------------------------------------------------------------------
# Line-to-agent mapping (extracted for testability)
# ---------------------------------------------------------------------------

_AGENT_PATTERN_RE = r'^\s+(agent-\S+)'


def build_line_agent_map(report: str, agent_spans: list[AgentSpanInfo]) -> dict[str, AgentSpanInfo]:
    """Map ``line-<N>`` option IDs to the correct :class:`AgentSpanInfo`.

    Handles duplicate labels across rounds by consuming spans in order.
    """
    agent_by_label: dict[str, list[AgentSpanInfo]] = defaultdict(list)
    for span in agent_spans:
        agent_by_label[span.label].append(span)

    pattern = re.compile(_AGENT_PATTERN_RE)
    result: dict[str, AgentSpanInfo] = {}
    for i, line in enumerate(report.splitlines()):
        m = pattern.match(line)
        if m:
            agent_label = m.group(1)
            if agent_by_label.get(agent_label):
                result[f'line-{i}'] = agent_by_label[agent_label].pop(0)
    return result


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
    from textual.containers import Horizontal, VerticalScroll
    from textual.events import Key
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
        }
        #introspect-pane {
            border: solid $success;
        }
        #left-pane:focus-within {
            border: heavy $primary;
        }
        #right-pane:focus-within {
            border: heavy $secondary;
        }
        #introspect-pane:focus-within {
            border: heavy $success;
        }
        .hidden {
            display: none;
        }
        """

        BINDINGS: ClassVar[list[BindingType]] = [
            Binding('q', 'quit', 'Quit'),
            Binding('h', 'focus_left', 'Left pane'),
            Binding('l', 'focus_right', 'Right pane'),
            Binding('r', 'refresh_trace', 'Refresh'),
            Binding('escape', 'back', 'Back', show=False),
        ]

        def __init__(self, base_dir: Path) -> None:
            super().__init__()
            self.base_dir = base_dir
            self.runs: list[RunEntry] = []
            self._selected_idx: int = 0
            self._agent_spans: list[AgentSpanInfo] = []
            # Maps right-pane OptionList option IDs to AgentSpanInfo
            self._line_agent_map: dict[str, AgentSpanInfo] = {}
            self._introspecting: bool = False
            self._introspect_idx: int = 0  # index into _agent_spans

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id='main-layout'):
                yield OptionList(id='left-pane')
                yield OptionList(id='right-pane')
            with VerticalScroll(id='introspect-pane', classes='hidden'):
                yield Static('', id='introspect-content')
            yield Footer()

        def on_mount(self) -> None:
            self._load_runs()

        def _load_runs(self) -> None:
            self.runs = _discover_runs(self.base_dir)
            option_list = self.query_one('#left-pane', OptionList)
            option_list.clear_options()
            for run in self.runs:
                status_icon = '✓' if run.status == 'completed' else '✗' if run.status == 'failed' else '…'
                label = f'{status_icon} {run.run_id[:20]}'
                option_list.add_option(Option(label, id=run.run_id))

            if self.runs:
                option_list.highlighted = 0
                self._selected_idx = 0
                self._show_trace(0)

            option_list.focus()

        def on_key(self, event: Key) -> None:
            focused = self.focused
            if event.key == 'j':
                if isinstance(focused, OptionList):
                    hl = focused.highlighted
                    if hl is not None and hl < focused.option_count - 1:
                        focused.highlighted = hl + 1
                elif self._introspecting:
                    self.query_one('#introspect-pane', VerticalScroll).scroll_down(animate=False)
                event.prevent_default()
            elif event.key == 'k':
                if isinstance(focused, OptionList):
                    hl = focused.highlighted
                    if hl is not None and hl > 0:
                        focused.highlighted = hl - 1
                elif self._introspecting:
                    self.query_one('#introspect-pane', VerticalScroll).scroll_up(animate=False)
                event.prevent_default()
            elif event.key == 'ctrl+f':
                if isinstance(focused, OptionList):
                    hl = focused.highlighted
                    if hl is not None:
                        focused.highlighted = min(hl + 20, focused.option_count - 1)
                elif self._introspecting:
                    self.query_one('#introspect-pane', VerticalScroll).scroll_page_down(animate=False)
                event.prevent_default()
            elif event.key == 'ctrl+u':
                if isinstance(focused, OptionList):
                    hl = focused.highlighted
                    if hl is not None:
                        focused.highlighted = max(hl - 20, 0)
                elif self._introspecting:
                    self.query_one('#introspect-pane', VerticalScroll).scroll_page_up(animate=False)
                event.prevent_default()
            elif event.key == 'ctrl+j' and self._introspecting:
                if self._introspect_idx < len(self._agent_spans) - 1:
                    self._enter_introspection(self._agent_spans[self._introspect_idx + 1])
                event.prevent_default()
            elif event.key == 'ctrl+k' and self._introspecting:
                if self._introspect_idx > 0:
                    self._enter_introspection(self._agent_spans[self._introspect_idx - 1])
                event.prevent_default()
            elif event.key == 'enter' and isinstance(focused, OptionList) and focused.id == 'right-pane':
                self._try_introspect()
                event.prevent_default()

        def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
            if not event.option or not event.option.id:
                return
            opt_list = event.option_list
            if opt_list.id == 'left-pane':
                idx = next((i for i, r in enumerate(self.runs) if r.run_id == event.option.id), None)
                if idx is not None:
                    self._selected_idx = idx
                    self._show_trace(idx)

        def _show_trace(self, idx: int) -> None:
            run = self.runs[idx]
            right = self.query_one('#right-pane', OptionList)
            right.clear_options()
            self._line_agent_map.clear()
            self._agent_spans = []

            if run.trace_path:
                report = format_trace_report(run.trace_path)
                self._agent_spans = load_agent_spans(run.trace_path)
            else:
                report = '(no trace data)'

            self._line_agent_map = build_line_agent_map(report, self._agent_spans)

            header = f'Run: {run.run_id}\nStatus: {run.status}\nQuestion: {run.question}'
            for hl in header.splitlines():
                right.add_option(Option(hl, disabled=True))
            right.add_option(Option('─' * 40, disabled=True))

            agent_pattern = re.compile(_AGENT_PATTERN_RE)
            for i, line in enumerate(report.splitlines()):
                opt_id = f'line-{i}'
                m = agent_pattern.match(line)
                if m and opt_id in self._line_agent_map:
                    right.add_option(Option(f'▸ {line.strip()}', id=opt_id))
                    continue
                # Non-agent lines: add as disabled (not selectable, just display)
                right.add_option(Option(line or ' ', disabled=True))

            # Scroll to bottom
            if right.option_count > 0:
                right.highlighted = right.option_count - 1

        def _try_introspect(self) -> None:
            right = self.query_one('#right-pane', OptionList)
            hl = right.highlighted
            if hl is None:
                return
            option = right.get_option_at_index(hl)
            if option.id and option.id in self._line_agent_map:
                span = self._line_agent_map[option.id]
                self._enter_introspection(span)

        def _enter_introspection(self, span: AgentSpanInfo) -> None:
            self._introspecting = True
            self._introspect_idx = self._agent_spans.index(span) if span in self._agent_spans else 0
            # Hide left pane + right pane, show introspection
            self.query_one('#main-layout').add_class('hidden')
            introspect = self.query_one('#introspect-pane', VerticalScroll)
            introspect.remove_class('hidden')

            content = self.query_one('#introspect-content', Static)
            header = f'Agent: {span.label}'
            if span.elapsed_secs is not None:
                header += f'  |  {span.elapsed_secs}s'
            if span.cost_usd is not None:
                header += f'  |  ${span.cost_usd:.2f}'
            if span.timed_out:
                header += '  |  TIMEOUT'
            header += '\n' + '─' * 60 + '\n'

            if span.structured_output:
                header += '\nStructured Output:\n'
                header += json.dumps(span.structured_output, indent=2) + '\n'
                header += '─' * 60 + '\n'

            if span.log_path:
                run_dir = self.runs[self._selected_idx].path if self.runs else None
                log_text = format_agent_log(Path(span.log_path), base_dir=run_dir)
            else:
                log_text = '(no log file path recorded)'

            content.update(header + log_text)
            introspect.focus()
            introspect.scroll_home(animate=False)

        def _exit_introspection(self) -> None:
            self._introspecting = False
            self.query_one('#introspect-pane', VerticalScroll).add_class('hidden')
            self.query_one('#main-layout').remove_class('hidden')
            self.query_one('#right-pane', OptionList).focus()

        def action_quit(self) -> None:
            if self._introspecting:
                self._exit_introspection()
            else:
                self.exit()

        def action_back(self) -> None:
            if self._introspecting:
                self._exit_introspection()

        def action_focus_left(self) -> None:
            if self._introspecting:
                return
            self.query_one('#left-pane', OptionList).focus()

        def action_focus_right(self) -> None:
            if self._introspecting:
                return
            self.query_one('#right-pane', OptionList).focus()

        def action_refresh_trace(self) -> None:
            """Re-read trace file and redraw the current trace."""
            if self._introspecting:
                self._exit_introspection()
            if self.runs:
                self._load_runs()
                if self._selected_idx < len(self.runs):
                    self._show_trace(self._selected_idx)

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
