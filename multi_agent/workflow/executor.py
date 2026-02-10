"""Step execution engine for Dynamic Ralph.

Launches Claude Code agents for individual workflow steps, streams their output,
captures metrics, processes workflow edits, and handles success/failure/timeout.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from multi_agent.constants import GIT_EMAIL, RALPH_IMAGE
from multi_agent.docker import build_image, docker_sock_gid, image_exists
from multi_agent.prompts import BASE_AGENT_INSTRUCTIONS
from multi_agent.stream import display_event
from multi_agent.workflow.editing import (
    EditValidationError,
    apply_edits,
    discard_edit_file,
    parse_edit_file,
    remove_edit_file,
    validate_edits,
)
from multi_agent.workflow.models import (
    HistoryEntry,
    Step,
    StepStatus,
    StoryWorkflow,
)
from multi_agent.workflow.prompts import compose_step_prompt
from multi_agent.workflow.scratch import (
    append_story_scratch,
    read_global_scratch,
    read_story_scratch,
)
from multi_agent.workflow.state import locked_state
from multi_agent.workflow.steps import STEP_TIMEOUTS


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentResult dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Captures all outputs from a Claude Code agent invocation."""

    exit_code: int = 1
    num_turns: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    completion_status: str = 'unknown'
    final_response: str = ''
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git_current_sha() -> str:
    """Get current HEAD SHA."""
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_save_diff(output_path: Path, base_sha: str) -> None:
    """Save diff (committed + uncommitted) since *base_sha* to a file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ['git', 'diff', base_sha],
        capture_output=True,
        text=True,
    )
    output_path.write_text(result.stdout)


def _git_reset_hard(target_sha: str) -> None:
    """Hard-reset to *target_sha* and clean untracked files."""
    subprocess.run(['git', 'reset', '--hard', target_sha], check=True)
    subprocess.run(['git', 'clean', '-fd'], check=True)


# ---------------------------------------------------------------------------
# Summary extraction
# ---------------------------------------------------------------------------


def _extract_summary(text: str) -> str | None:
    """Extract the SUMMARY section from agent output.

    Looks for a line starting with "SUMMARY" (case-insensitive) and returns
    everything after it.  Returns ``None`` if no summary is found.
    """
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        # Strip leading markdown heading markers: "## SUMMARY" -> "SUMMARY"
        normalized = stripped.lstrip('#').strip()
        if normalized.upper().startswith('SUMMARY'):
            # Everything after the SUMMARY header line
            remaining = lines[idx + 1 :]
            summary = '\n'.join(remaining).strip()
            if summary:
                return summary
            # The header line itself might contain the summary after a colon
            after_keyword = normalized[len('SUMMARY') :].lstrip(':').strip()
            return after_keyword if after_keyword else None
    return None


# ---------------------------------------------------------------------------
# Docker / inside-docker detection
# ---------------------------------------------------------------------------


def _is_inside_docker() -> bool:
    """Return True if we are already running inside a Docker container."""
    return Path('/.dockerenv').exists()


# ---------------------------------------------------------------------------
# Agent launcher
# ---------------------------------------------------------------------------


def _launch_agent(
    prompt: str,
    agent_id: int,
    max_turns: int | None,
    log_path: Path,
    timeout: int,
) -> AgentResult:
    """Launch Claude Code and stream its output, returning an AgentResult.

    When already inside Docker (``/.dockerenv`` exists), runs Claude Code
    directly.  Otherwise wraps the invocation in a ``docker run`` command
    using the same pattern as ``bin/run_agent.py``.
    """
    # -- build the claude command ------------------------------------------------
    claude_cmd: list[str] = [
        'npx',
        '@anthropic-ai/claude-code',
        '--dangerously-skip-permissions',
        '--print',
        '--verbose',
        '--output-format',
        'stream-json',
        '--append-system-prompt',
        BASE_AGENT_INSTRUCTIONS,
    ]
    if max_turns is not None:
        claude_cmd.extend(['--max-turns', str(max_turns)])
    claude_cmd.append(prompt)

    # -- wrap in docker if needed ------------------------------------------------
    if _is_inside_docker():
        cmd = claude_cmd
    else:
        if not image_exists():
            build_image()

        workspace = os.getcwd()
        compose_project = f'ralph_agent_{agent_id}'
        claude_config = Path.home() / '.claude'
        host_config_claude = Path.home() / '.config' / 'claude'

        cmd = [
            'docker',
            'run',
            '--rm',
            '--group-add',
            docker_sock_gid(),
            '-e',
            f'AGENT_ID={agent_id}',
            '-e',
            f'COMPOSE_PROJECT_NAME={compose_project}',
            '-e',
            f'HOST_WORKSPACE={workspace}',
            '-e',
            'IS_SANDBOX=1',
            '-e',
            'UV_PROJECT_ENVIRONMENT=/tmp/venv',
            '-e',
            'GIT_AUTHOR_NAME=Claude Agent',
            '-e',
            f'GIT_AUTHOR_EMAIL={GIT_EMAIL}',
            '-e',
            'GIT_COMMITTER_NAME=Claude Agent',
            '-e',
            f'GIT_COMMITTER_EMAIL={GIT_EMAIL}',
            '-v',
            '/var/run/docker.sock:/var/run/docker.sock',
            '-v',
            f'{workspace}:/workspace',
            '-v',
            '/workspace/.venv',  # anonymous volume: hide host .venv
            '-v',
            f'{claude_config}:/home/agent/.claude',
            '-v',
            f'{host_config_claude}:/home/agent/.config/claude',
            '-w',
            '/workspace',
            RALPH_IMAGE,
            *claude_cmd,
        ]

    # -- launch and stream -------------------------------------------------------
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = AgentResult()

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )

    log_file = open(log_path, 'w')
    last_assistant_text: str = ''
    start_time = time.monotonic()

    try:
        for line in process.stdout:  # type: ignore[union-attr]
            # -- timeout check ---------------------------------------------------
            elapsed = time.monotonic() - start_time
            if elapsed > timeout:
                logger.warning(
                    'Step timed out after %d seconds, terminating agent',
                    timeout,
                )
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                result.timed_out = True
                break

            raw_line = line.rstrip('\n')
            stripped = raw_line.strip()
            if not stripped:
                continue

            try:
                event = json.loads(stripped)
                display_event(event)

                # write raw JSON to log
                log_file.write(raw_line + '\n')
                log_file.flush()

                etype = event.get('type', '')

                if etype == 'result':
                    result.num_turns = event.get('num_turns', 0)
                    result.cost_usd = event.get('total_cost_usd', 0.0)
                    result.input_tokens = event.get('input_tokens', 0)
                    result.output_tokens = event.get('output_tokens', 0)
                    result.completion_status = event.get('subtype', 'unknown')

                elif etype == 'assistant':
                    # Capture the last assistant text block for summary extraction
                    message = event.get('message', {})
                    for block in message.get('content', []):
                        if block.get('type') == 'text':
                            last_assistant_text = block.get('text', '')

            except json.JSONDecodeError:
                print(stripped, file=sys.stderr)
                log_file.write(f'# {raw_line}\n')
                log_file.flush()

        process.wait()
        result.exit_code = process.returncode or 0
    finally:
        log_file.close()

    result.final_response = last_assistant_text
    return result


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Main entry point: execute_step
# ---------------------------------------------------------------------------


def execute_step(
    story: StoryWorkflow,
    step: Step,
    agent_id: int,
    state_path: Path,
    shared_dir: Path,
) -> Step:
    """Execute a single workflow step end-to-end.

    1. Record git SHA and mark step ``in_progress``.
    2. Build prompt and launch agent.
    3. Stream output, capture metrics and final response.
    4. On success: process workflow edits, mark ``completed``.
    5. On failure: discard edits, save diff, reset git, mark ``failed``.
    6. On timeout: mark ``cancelled``.

    Returns the updated :class:`Step`.
    """
    story_id = story.story_id
    step_id = step.id

    # ---- (a) Record git SHA at start -----------------------------------------
    git_sha_at_start = _git_current_sha()
    step.git_sha_at_start = git_sha_at_start

    # ---- (b) Mark step in_progress and persist --------------------------------
    step.status = StepStatus.in_progress
    step.started_at = _now_iso()

    with locked_state(state_path) as state:
        persisted_story = state.stories.get(story_id)
        if persisted_story:
            persisted_step = persisted_story.find_step(step_id)
            if persisted_step:
                persisted_step.status = StepStatus.in_progress
                persisted_step.started_at = step.started_at
                persisted_step.git_sha_at_start = git_sha_at_start

    # ---- (c) History: step_started --------------------------------------------
    story.history.append(
        HistoryEntry(
            timestamp=_now_iso(),
            action='step_started',
            agent_id=agent_id,
            step_id=step_id,
        )
    )

    # ---- (d) Compose prompt ---------------------------------------------------
    global_scratch = read_global_scratch(shared_dir)
    story_scratch = read_story_scratch(story_id, shared_dir)

    prompt = compose_step_prompt(
        story=story,
        step=step,
        global_scratch=global_scratch,
        story_scratch=story_scratch,
        base_instructions=BASE_AGENT_INSTRUCTIONS,
    )

    # ---- (e) Launch agent -----------------------------------------------------
    log_dir = Path('logs') / story_id
    log_path = log_dir / f'{step_id}.jsonl'

    timeout = STEP_TIMEOUTS.get(step.type, 900)
    max_turns: int | None = None  # let the agent run freely within the timeout

    logger.info(
        'Launching agent for %s / %s (timeout=%ds)',
        story_id,
        step_id,
        timeout,
    )

    agent_result = _launch_agent(
        prompt=prompt,
        agent_id=agent_id,
        max_turns=max_turns,
        log_path=log_path,
        timeout=timeout,
    )

    # ---- (f) Log path on step -------------------------------------------------
    step.log_file = str(log_path)

    # ---- (g) Extract summary --------------------------------------------------
    summary = _extract_summary(agent_result.final_response)
    step.notes = summary

    # ---- (g2) Persist summary to story scratch file ---------------------------
    if summary:
        append_story_scratch(
            story_id,
            f'\n### {step.type} ({step_id})\n{summary}\n',
            shared_dir,
        )

    # ---- (h) Capture cost/token metrics ---------------------------------------
    step.cost_usd = agent_result.cost_usd
    step.input_tokens = agent_result.input_tokens
    step.output_tokens = agent_result.output_tokens

    # ---- (k) Timeout handling -------------------------------------------------
    if agent_result.timed_out:
        step.status = StepStatus.cancelled
        step.completed_at = _now_iso()
        step.error = f'Step timed out after {timeout}s'

        story.history.append(
            HistoryEntry(
                timestamp=_now_iso(),
                action='step_cancelled',
                agent_id=agent_id,
                step_id=step_id,
                details={'reason': 'timeout', 'timeout_seconds': timeout},
            )
        )

        _persist_step(story, step, state_path)
        logger.warning('Step %s/%s timed out after %ds', story_id, step_id, timeout)
        return step

    # ---- (j) Failure handling -------------------------------------------------
    if agent_result.exit_code != 0:
        # Discard any workflow edits the agent may have written
        discard_edit_file(story_id, shared_dir)

        # Save the diff for debugging
        diff_path = Path('logs') / story_id / f'{step_id}.diff'
        _git_save_diff(diff_path, git_sha_at_start)

        # Reset to clean state
        _git_reset_hard(git_sha_at_start)

        step.status = StepStatus.failed
        step.completed_at = _now_iso()
        step.error = f'Agent exited with code {agent_result.exit_code} (status={agent_result.completion_status})'

        story.history.append(
            HistoryEntry(
                timestamp=_now_iso(),
                action='step_failed',
                agent_id=agent_id,
                step_id=step_id,
                details={
                    'exit_code': agent_result.exit_code,
                    'completion_status': agent_result.completion_status,
                    'cost_usd': agent_result.cost_usd,
                },
            )
        )

        _persist_step(story, step, state_path)
        logger.error(
            'Step %s/%s failed (exit=%d)',
            story_id,
            step_id,
            agent_result.exit_code,
        )
        return step

    # ---- (i) Success handling -------------------------------------------------

    # Process workflow edits BEFORE marking complete
    _process_workflow_edits(story, step, agent_id, shared_dir)

    step.status = StepStatus.completed
    step.completed_at = _now_iso()

    story.history.append(
        HistoryEntry(
            timestamp=_now_iso(),
            action='step_completed',
            agent_id=agent_id,
            step_id=step_id,
            details={
                'cost_usd': agent_result.cost_usd,
                'num_turns': agent_result.num_turns,
                'input_tokens': agent_result.input_tokens,
                'output_tokens': agent_result.output_tokens,
            },
        )
    )

    _persist_step(story, step, state_path)
    logger.info(
        'Step %s/%s completed (cost=$%.4f, turns=%d)',
        story_id,
        step_id,
        agent_result.cost_usd,
        agent_result.num_turns,
    )
    return step


# ---------------------------------------------------------------------------
# Workflow edit processing
# ---------------------------------------------------------------------------


def _process_workflow_edits(
    story: StoryWorkflow,
    step: Step,
    agent_id: int,
    shared_dir: Path,
) -> None:
    """Parse, validate, and apply workflow edits written by the agent.

    On validation failure the edit file is discarded and a warning is logged
    but execution continues (the step still succeeds).
    """
    story_id = story.story_id

    try:
        operations = parse_edit_file(story_id, shared_dir)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            'Invalid workflow edit file for %s: %s',
            story_id,
            exc,
        )
        discard_edit_file(story_id, shared_dir)
        return

    if operations is None:
        return  # no edit file

    try:
        validate_edits(story, operations)
    except EditValidationError as exc:
        logger.warning(
            'Workflow edits for %s failed validation: %s',
            story_id,
            exc,
        )
        discard_edit_file(story_id, shared_dir)
        return

    # Apply edits to the in-memory story
    apply_edits(story, operations)

    # Record history
    for op in operations:
        story.history.append(
            HistoryEntry(
                timestamp=_now_iso(),
                action='workflow_edit',
                agent_id=agent_id,
                step_id=step.id,
                details={
                    'operation': op.operation,
                    'edit': op.model_dump(),
                },
            )
        )

    logger.info(
        'Applied %d workflow edit(s) for %s from step %s',
        len(operations),
        story_id,
        step.id,
    )

    # Clean up the edit file
    remove_edit_file(story_id, shared_dir)


# ---------------------------------------------------------------------------
# Persist step state
# ---------------------------------------------------------------------------


def _persist_step(
    story: StoryWorkflow,
    step: Step,
    state_path: Path,
) -> None:
    """Persist the current step and story state to the state file."""
    with locked_state(state_path) as state:
        persisted_story = state.stories.get(story.story_id)
        if persisted_story is None:
            return

        # Update the step fields
        persisted_step = persisted_story.find_step(step.id)
        if persisted_step is not None:
            persisted_step.status = step.status
            persisted_step.started_at = step.started_at
            persisted_step.completed_at = step.completed_at
            persisted_step.git_sha_at_start = step.git_sha_at_start
            persisted_step.notes = step.notes
            persisted_step.error = step.error
            persisted_step.cost_usd = step.cost_usd
            persisted_step.input_tokens = step.input_tokens
            persisted_step.output_tokens = step.output_tokens
            persisted_step.log_file = step.log_file
            persisted_step.restart_count = step.restart_count

        # Sync steps list (workflow edits may have added/removed/reordered steps)
        persisted_story.steps = story.steps

        # Sync history
        persisted_story.history = story.history
