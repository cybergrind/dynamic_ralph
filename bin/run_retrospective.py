#!/usr/bin/env python3
"""Retrospective analysis: launch an agent to diagnose a completed run, fix issues, and verify.

Usage:
    uv run bin/run_retrospective.py <run_dir> [--max-turns N] [--build]

The script reads a completed run directory (summary.log, workflow_state.json, logs/),
constructs a 3-phase prompt (diagnose -> fix -> verify), and launches an agent to
analyse failures, implement fixes, and write retrospective.md.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from multi_agent import BASE_AGENT_INSTRUCTIONS, build_image, image_exists
from multi_agent.backend import AgentResult, get_backend
from multi_agent.stream import display_agent_event
from multi_agent.workflow.models import StepStatus, StoryStatus, WorkflowState


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_run_dir(run_dir: Path) -> None:
    """Validate that *run_dir* contains the expected files.

    Raises ``SystemExit`` if the directory is missing or lacks required files.
    """
    if not run_dir.is_dir():
        print(f'Error: run directory does not exist: {run_dir}', file=sys.stderr)
        sys.exit(1)

    for required in ('summary.log', 'workflow_state.json'):
        if not (run_dir / required).exists():
            print(f'Error: {required} not found in {run_dir}', file=sys.stderr)
            sys.exit(1)


# ---------------------------------------------------------------------------
# State digest
# ---------------------------------------------------------------------------


def build_state_digest(state: WorkflowState) -> str:
    """Summarise workflow state into a human-readable digest.

    Returns a multi-line string listing each story and its steps with status,
    errors, and timing information.
    """
    lines: list[str] = []

    for story_id, story in state.stories.items():
        lines.append(f'### Story: {story_id} — {story.title}')
        lines.append(f'Status: {story.status}')

        if not story.steps:
            lines.append('  (no steps)')
            lines.append('')
            continue

        for step in story.steps:
            timing = ''
            if step.started_at and step.completed_at:
                timing = f' ({step.started_at} -> {step.completed_at})'

            status_line = f'  - {step.id} ({step.type}): {step.status}{timing}'
            lines.append(status_line)

            if step.error:
                lines.append(f'    ERROR: {step.error}')
            if step.notes:
                # Truncate long notes for the digest
                truncated = step.notes[:200] + '...' if len(step.notes) > 200 else step.notes
                lines.append(f'    Notes: {truncated}')
            if step.cost_usd is not None:
                lines.append(
                    f'    Cost: ${step.cost_usd:.4f}'
                    f'  Tokens: {step.input_tokens or 0} in / {step.output_tokens or 0} out'
                )

        lines.append('')

    # Summary counts
    status_counts: dict[str, int] = {}
    for story in state.stories.values():
        status_counts[story.status] = status_counts.get(story.status, 0) + 1

    parts = [f'{status}={count}' for status, count in sorted(status_counts.items())]
    lines.append(f'**Overall:** {len(state.stories)} stories — {", ".join(parts)}')

    # Highlight failures
    failed_stories = [s for s in state.stories.values() if s.status == StoryStatus.failed]
    if failed_stories:
        lines.append('')
        lines.append('**Failed stories:**')
        for story in failed_stories:
            failed_steps = [st for st in story.steps if st.status in (StepStatus.failed, StepStatus.cancelled)]
            for step in failed_steps:
                lines.append(f'  - [{story.story_id}] {step.id} ({step.type}): {step.error}')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Log file collection
# ---------------------------------------------------------------------------


def collect_log_files(run_dir: Path) -> list[Path]:
    """Collect all log files from the run directory's logs/ subdirectory.

    Returns sorted paths to .jsonl, .stderr.log, and .diff files.
    """
    logs_dir = run_dir / 'logs'
    if not logs_dir.is_dir():
        return []

    extensions = {'.jsonl', '.log', '.diff'}
    files = []
    for path in sorted(logs_dir.rglob('*')):
        if path.is_file() and path.suffix in extensions:
            files.append(path)

    return files


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_retrospective_prompt(
    run_dir: Path,
    summary_log: str,
    state_digest: str,
    log_files: list[Path],
) -> str:
    """Assemble the full 3-phase prompt for the retrospective agent.

    The prompt includes run context (summary, state digest, log file paths)
    and clear instructions for the diagnose, fix, and verify phases.
    """
    log_list = '\n'.join(f'  - {p}' for p in log_files) if log_files else '  (no log files found)'

    retro_path = run_dir / 'retrospective.md'

    return f"""\
# Retrospective Analysis

## Run Directory
{run_dir.resolve()}

## Summary Log
```
{summary_log}
```

## Workflow State Digest
{state_digest}

## Log Files Available
{log_list}

## Instructions

You are analysing a completed Dynamic Ralph run to diagnose failures, implement fixes, and verify them.

### Phase 1: Diagnose
- Read the log files listed above in detail (use the Read tool on the .jsonl files to see full agent conversations)
- For each failed story/step: identify the root cause from the .jsonl conversation logs
- Check .stderr.log files for repeated warnings or errors
- Note any steps that timed out or were cancelled
- Identify patterns across failures

### Phase 2: Fix
- Implement fixes in the codebase for the diagnosed issues
- Run `uv run pytest` and `uv run pre-commit run -a` to verify fixes don't break anything
- Commit your changes with a descriptive message

### Phase 3: Verify
- Run: `uv run bin/run_dynamic_ralph.py "verify: <description of what to test>"`
- Wait for it to complete
- Read the verification run's summary.log and workflow_state.json
- Report pass/fail results

### Output
Write {retro_path} containing:
1. **Summary of failures and root causes** — what went wrong and why
2. **Repeated warnings/errors** — patterns found in stderr logs, with fix suggestions
3. **Fixes implemented** — what you changed and why
4. **Verification results** — pass/fail from the verification run
5. **Timing analysis** — which steps took longest, any timeouts

CRITICAL: DO NOT delete workflow_state.json, workflow_state.json.lock, prd.json,
scratch.md, or scratch_*.md — these are actively used by the running orchestrator.
"""


# ---------------------------------------------------------------------------
# Stderr tee helper
# ---------------------------------------------------------------------------


def _tee_stderr(pipe, log_file, terminal):
    """Read lines from *pipe* and write each to both *log_file* and *terminal*."""
    for line in pipe:
        terminal.write(line)
        terminal.flush()
        log_file.write(line)
        log_file.flush()


# ---------------------------------------------------------------------------
# Agent launcher
# ---------------------------------------------------------------------------


def launch_agent(prompt: str, log_path: Path, max_turns: int | None = None) -> AgentResult:
    """Launch the retrospective agent via the configured backend.

    Mirrors the pattern from ``executor.py:_launch_agent()``: gets the backend,
    builds the command, optionally wraps in Docker, streams events, captures
    stderr, and writes a .jsonl log.

    Returns the :class:`AgentResult` from the agent run.
    """
    backend = get_backend()

    base_cmd = backend.build_command(
        prompt,
        system_prompt=BASE_AGENT_INSTRUCTIONS,
        max_turns=max_turns,
    )

    # Wrap in Docker if not already inside a container
    if Path('/.dockerenv').exists():
        cmd = base_cmd
    else:
        cmd = backend.build_docker_command(
            base_cmd,
            agent_id=99,  # retrospective agent ID
            workspace=os.getcwd(),
        )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_log_path = log_path.with_suffix('.stderr.log')

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    log_file = open(log_path, 'w')
    stderr_log_file = open(stderr_log_path, 'w')

    stderr_thread = threading.Thread(
        target=_tee_stderr,
        args=(process.stderr, stderr_log_file, sys.stderr),
        daemon=True,
    )
    stderr_thread.start()

    all_events: list = []

    try:

        def _line_iter():
            for line in process.stdout:  # type: ignore[union-attr]
                yield line

        for event in backend.parse_events(_line_iter()):
            all_events.append(event)
            display_agent_event(event)

            if event.raw:
                import json as _json

                log_file.write(_json.dumps(event.raw) + '\n')
            else:
                log_file.write(f'# {event.text}\n')
            log_file.flush()

        process.wait()
    finally:
        stderr_thread.join(timeout=5)
        log_file.close()
        stderr_log_file.close()

    exit_code = process.returncode or 0
    result = backend.extract_result(all_events, exit_code)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Analyse a completed Dynamic Ralph run directory and launch a retrospective agent.',
    )
    parser.add_argument(
        'run_dir',
        type=Path,
        help='Path to the completed run directory',
    )
    parser.add_argument(
        '--max-turns',
        type=int,
        default=None,
        help='Max turns for the retrospective agent',
    )
    parser.add_argument(
        '--build',
        action='store_true',
        help='Rebuild Docker image before launching',
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        stream=sys.stderr,
    )

    # Validate run directory
    validate_run_dir(args.run_dir)

    # Build Docker image if requested or missing
    if args.build or not image_exists():
        build_image()

    # Read run directory contents
    summary_log = (args.run_dir / 'summary.log').read_text(encoding='utf-8')

    state_data = json.loads((args.run_dir / 'workflow_state.json').read_text(encoding='utf-8'))
    state = WorkflowState.model_validate(state_data)

    state_digest = build_state_digest(state)
    log_files = collect_log_files(args.run_dir)

    # Build prompt
    prompt = build_retrospective_prompt(
        run_dir=args.run_dir,
        summary_log=summary_log,
        state_digest=state_digest,
        log_files=log_files,
    )

    # Launch agent
    log_path = args.run_dir / 'logs' / 'retrospective.jsonl'
    print(f'Launching retrospective agent for {args.run_dir}', flush=True)
    print(f'  Log: {log_path}', flush=True)

    result = launch_agent(prompt, log_path, max_turns=args.max_turns)

    if result.exit_code == 0:
        print(f'Retrospective agent completed successfully (cost=${result.cost_usd:.4f})', flush=True)
    else:
        print(f'Retrospective agent failed (exit_code={result.exit_code})', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
