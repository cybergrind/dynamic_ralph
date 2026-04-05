"""Parallel agent execution engine.

Launches multiple agents as separate ``claude -p`` subprocesses using
``concurrent.futures.ThreadPoolExecutor``.  Each agent's stdout events
are parsed via the ``AgentBackend`` protocol and only a memory-safe subset
(``_RETAINED_KINDS``) is kept in RAM; all events are written to disk logs.

Safety patterns mirror ``executor.py:_launch_agent()`` — see the table in
``docs/scratches/multi_agent_skill.md`` for rationale.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import threading
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from multi_agent.backend import AgentBackend, AgentEvent, AgentResult, OutputSchema, get_backend
from multi_agent.constants import MULTI_AGENT_MAX_WORKERS


if TYPE_CHECKING:
    from multi_agent.trace import TraceWriter


# Only retain events that extract_result() inspects. Tool events (tool_use,
# tool_result) contain full file contents and would amplify memory 10x when
# 5 agents run concurrently. All events are still written to the disk log.
# CONTRACT: if extract_result() ever needs tool events, update this set.
_RETAINED_KINDS: frozenset[str] = frozenset({'assistant', 'result', 'system', 'error'})

_TERMINATE_GRACE_SECONDS: float = 5.0


class _SubprocessWatchdog:
    """Preemptive wall-clock timeout that fires even when the event loop blocks.

    The cooperative timeout (checking elapsed time inside ``for event in ...``)
    fails when proc.stdout.readline() blocks because the agent emits no events
    (e.g., stuck mid-turn waiting on an API response). ``max_turns`` does not
    help because a hung process never completes a turn.

    This watchdog uses ``threading.Event.wait(timeout=N)``, which is
    unconditional. When it fires, it calls terminate() → wait(5) → kill()
    on the subprocess.
    """

    def __init__(
        self, proc: subprocess.Popen, timeout: float, *, grace_period: float = _TERMINATE_GRACE_SECONDS
    ) -> None:
        self._proc = proc
        self._timeout = timeout
        self._grace_period = grace_period
        self._cancelled = threading.Event()
        self._fired = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        # wait() returns True if the event was set (i.e., cancelled).
        # Returns False on timeout — meaning we should fire.
        cancelled = self._cancelled.wait(timeout=self._timeout)
        if not cancelled and self._proc.poll() is None:
            self._fired = True
            self._proc.terminate()
            try:
                self._proc.wait(timeout=self._grace_period)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()

    def cancel(self) -> None:
        """Signal the watchdog to stop. Safe to call multiple times."""
        self._cancelled.set()

    @property
    def fired(self) -> bool:
        return self._fired


def _tee_stderr(pipe, log_file) -> None:
    """Capture stderr to log file without printing (parallel agents would interleave)."""
    for line in pipe:
        log_file.write(line)
        log_file.flush()


_PROGRESS_INTERVAL: float = 30.0  # seconds between progress heartbeats


def launch_parallel_agents(
    prompts: dict[str, str],
    *,
    backend: AgentBackend | None = None,
    max_turns: int = 10,
    timeout: int = 900,
    log_dir: Path,
    log_prefix: str = '',
    output_schema: OutputSchema | None = None,
    tracer: TraceWriter | None = None,
    trace_parent_id: str | None = None,
) -> dict[str, AgentResult]:
    """Launch multiple agents in parallel, returning results keyed by label.

    Mirrors the safety guarantees of executor.py:_launch_agent():
    - CLAUDECODE env stripping (prevents nested-session detection)
    - Preemptive wall-clock timeout via _SubprocessWatchdog
    - _RETAINED_KINDS event filter (bounds memory for concurrent agents)
    - stderr capture to per-agent log files (prevents pipe buffer deadlock)
    - Deterministic subprocess cleanup in finally blocks

    Pass a FakeBackend for testing without spawning real claude -p processes.
    """
    if backend is None:
        backend = get_backend()

    # Strip CLAUDECODE to avoid nested-session detection (executor.py:231)
    agent_env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE'}

    def _run_one(label: str, prompt: str) -> tuple[str, AgentResult]:
        cmd = backend.build_command(prompt, max_turns=max_turns, output_schema=output_schema)
        log_path = log_dir / f'{log_prefix}{label}.jsonl'

        span_id = f'{trace_parent_id}-{label}' if trace_parent_id else f'agent-{label}'
        trace_span = (
            tracer.begin(span_id, 'agent', f'agent-{label}', parent_id=trace_parent_id, log_path=str(log_path))
            if tracer
            else None
        )
        stderr_log_path = log_dir / f'{log_prefix}{label}.stderr.log'
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize all resources to None for safe cleanup in finally block.
        # If Popen succeeds but open(stderr_log_path) throws (disk full,
        # permissions), log_file would leak without this pattern.
        proc = None
        log_file = None
        stderr_log_file = None
        watchdog = None
        stderr_thread = None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=agent_env,
            )

            log_file = open(log_path, 'w')
            stderr_log_file = open(stderr_log_path, 'w')

            # Preemptive watchdog: fires after wall-clock deadline regardless
            # of event loop state. See _SubprocessWatchdog docstring.
            watchdog = _SubprocessWatchdog(proc, timeout)
            watchdog.start()

            # Drain stderr in a background thread to prevent pipe buffer deadlock.
            # On Linux, pipe buffers are 64KB. If stderr fills up, the agent blocks
            # on its next stderr write, which stalls stdout, which deadlocks our
            # event reader. (See executor.py:246-251)
            stderr_thread = threading.Thread(
                target=_tee_stderr,
                args=(proc.stderr, stderr_log_file),
                daemon=True,
            )
            stderr_thread.start()

            all_events: list[AgentEvent] = []
            event_count = 0
            last_progress = _time.monotonic()

            for event in backend.parse_events(iter(proc.stdout)):
                # Write ALL events to disk log for post-mortem analysis
                if event.raw:
                    log_file.write(json.dumps(event.raw) + '\n')

                # Only retain events needed by extract_result() in memory.
                # CONTRACT: _RETAINED_KINDS must match what extract_result() reads.
                if event.kind in _RETAINED_KINDS:
                    all_events.append(event)

                event_count += 1

                # Periodic progress heartbeat for tracing
                if tracer and trace_span:
                    now = _time.monotonic()
                    if now - last_progress >= _PROGRESS_INTERVAL:
                        tracer.progress(trace_span, f'{event_count} events received')
                        last_progress = now

            proc.wait()
        finally:
            # Cancel watchdog first to prevent it from firing during cleanup.
            if watchdog is not None:
                watchdog.cancel()
            # Ensure process is reaped even if parse_events() raises.
            # Without this, an exception leaves proc as a zombie (never waited on).
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=_TERMINATE_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            if stderr_thread is not None:
                stderr_thread.join(timeout=5)
            if log_file is not None:
                log_file.close()
            if stderr_log_file is not None:
                stderr_log_file.close()

        timed_out = watchdog.fired if watchdog else False
        result = backend.extract_result(all_events, proc.returncode or 0)
        result.timed_out = timed_out

        if tracer and trace_span:
            span_details: dict = {
                'cost_usd': result.cost_usd,
                'input_tokens': result.input_tokens,
                'output_tokens': result.output_tokens,
                'timed_out': result.timed_out,
                'exit_code': result.exit_code,
                'num_turns': result.num_turns,
            }
            if result.structured_output is not None:
                span_details['structured_output'] = result.structured_output
            tracer.end(trace_span, **span_details)

        return label, result

    results: dict[str, AgentResult] = {}
    max_workers = min(len(prompts), MULTI_AGENT_MAX_WORKERS)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run_one, label, prompt): label for label, prompt in prompts.items()}
        for future in concurrent.futures.as_completed(futures):
            label = futures[future]
            try:
                _, result = future.result()
                results[label] = result
            except Exception:
                # Agent crashed hard (OOM, segfault). Record synthetic failure
                # so quorum enforcement can decide whether to retry.
                results[label] = AgentResult(
                    exit_code=1,
                    completion_status='crashed',
                )

    return results
