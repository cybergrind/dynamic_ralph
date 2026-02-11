#!/usr/bin/env python3
"""Dynamic Ralph orchestrator: step-based workflow execution.

Supports three modes:
  1. One-shot: single task, persistent state in run directory, full 10-step workflow.
  2. PRD serial: pick stories from prd.json, execute steps one at a time.
  3. PRD parallel: spawn up to N agents via git worktrees for concurrent stories.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from multi_agent import (
    BASE_AGENT_INSTRUCTIONS,
    build_image,
    image_exists,
)
from multi_agent.backend import get_backend
from multi_agent.stream import display_agent_event
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
    StepStatus,
    StoryStatus,
    StoryWorkflow,
    WorkflowState,
)
from multi_agent.workflow.prompts import compose_step_prompt
from multi_agent.workflow.scratch import (
    append_global_scratch,
    cleanup_story_scratch,
    read_global_scratch,
    read_story_scratch,
)
from multi_agent.workflow.state import (
    find_assignable_story,
    initialize_state_from_prd,
    load_state,
    locked_state,
    save_state,
)
from multi_agent.workflow.steps import STEP_TIMEOUTS, create_default_workflow


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKTREE_DIR = Path('worktrees')


def _git_main_branch() -> str:
    """Return the current branch name (e.g. 'main' or 'master')."""
    result = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or 'main'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _add_history(
    story: StoryWorkflow, action: str, agent_id: int, step_id: str | None = None, **details: object
) -> None:
    """Append a history entry to a story."""
    story.history.append(
        HistoryEntry(
            timestamp=_now_iso(),
            action=action,  # type: ignore[arg-type]
            agent_id=agent_id,
            step_id=step_id,
            details=details,  # type: ignore[arg-type]
        )
    )


def append_summary(message: str, run_dir: Path) -> None:
    """Append a timestamped line to <run_dir>/summary.log."""
    ts = datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')
    clean = message.replace('\n', ' ').replace('\r', '')
    line = f'[{ts} UTC] {clean}\n'
    with open(run_dir / 'summary.log', 'a') as f:
        f.write(line)


def _print_progress(message: str, run_dir: Path | None = None) -> None:
    """Print progress to stdout and log to stderr."""
    print(message, flush=True)
    logger.info(message)
    if run_dir is not None:
        append_summary(message, run_dir)


def _save_diff_and_reset(diff_path: Path, git_sha: str) -> None:
    """Save the current diff since *git_sha* to *diff_path*, then hard-reset.

    Per spec: on failure, capture all changes (committed + uncommitted) as a
    diff for debugging, then restore the worktree to the pre-step state.
    """
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ['git', 'diff', git_sha],
            capture_output=True,
            text=True,
        )
        diff_path.write_text(result.stdout)
    except Exception:
        logger.exception('Failed to save diff to %s', diff_path)

    try:
        subprocess.run(['git', 'reset', '--hard', git_sha], check=True)
        subprocess.run(['git', 'clean', '-fd'], check=True)
    except Exception:
        logger.exception('Failed to reset git to %s', git_sha)


def _extract_summary(text: str) -> str | None:
    """Extract the SUMMARY section from agent output.

    Looks for a line starting with "SUMMARY" (case-insensitive, ignoring
    leading ``#`` markers) and returns everything after it.
    """
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        normalized = stripped.lstrip('#').strip()
        if normalized.upper().startswith('SUMMARY'):
            remaining = lines[idx + 1 :]
            summary = '\n'.join(remaining).strip()
            if summary:
                return summary
            after_keyword = normalized[len('SUMMARY') :].lstrip(':').strip()
            return after_keyword if after_keyword else None
    return None


def generate_run_dir() -> Path:
    """Create a unique run directory under ``run_ralph/``.

    Format: ``run_ralph/<YYYYMMDD>T<HHMMSS>_<8-char-uuid4>/``
    Also creates ``workflow_edits/`` and ``logs/`` subdirectories inside it.
    """
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S')
    unique_id = uuid.uuid4().hex[:8]
    run_dir = Path('run_ralph') / f'{timestamp}_{unique_id}'
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'workflow_edits').mkdir(exist_ok=True)
    (run_dir / 'logs').mkdir(exist_ok=True)
    return run_dir


def _run_agent_docker(
    task: str, agent_id: int, max_turns: int | None, workspace: str | None = None, shared_dir: str | None = None
) -> tuple[int, dict]:
    """Launch an agent in a Docker container and stream events.

    Uses the configured backend (via ``get_backend()``) to build commands,
    parse events, and extract results.

    Returns (returncode, result_info) where result_info contains cost/token data
    from the final 'result' event plus the last assistant text for summary extraction.
    """
    if workspace is None:
        workspace = os.getcwd()

    backend = get_backend()

    base_cmd = backend.build_command(
        task,
        system_prompt=BASE_AGENT_INSTRUCTIONS,
        max_turns=max_turns,
    )

    docker_cmd = backend.build_docker_command(
        base_cmd,
        agent_id=agent_id,
        workspace=workspace,
    )

    process = subprocess.Popen(
        docker_cmd,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
        bufsize=1,
    )

    def _line_iter():
        for line in process.stdout:  # type: ignore[union-attr]
            yield line

    all_events = []
    for event in backend.parse_events(_line_iter()):
        all_events.append(event)
        display_agent_event(event)

    process.wait()

    agent_result = backend.extract_result(all_events, process.returncode or 0)

    result_info: dict = {
        'cost_usd': agent_result.cost_usd,
        'num_turns': agent_result.num_turns,
        'input_tokens': agent_result.input_tokens,
        'output_tokens': agent_result.output_tokens,
        'last_assistant_text': agent_result.final_response,
    }
    return process.returncode, result_info


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------


def execute_step(
    story: StoryWorkflow,
    step_id: str,
    agent_id: int,
    state_path: Path,
    shared_dir: Path,
    max_turns: int | None,
    run_dir: Path | None = None,
) -> bool:
    """Execute a single workflow step by launching the agent.

    Manages step lifecycle: mark in_progress, compose prompt, launch agent,
    process edits, mark completed/failed.

    Returns True if the step completed successfully, False if it failed.
    """
    # Load current state and find the step
    with locked_state(state_path) as state:
        sw = state.stories.get(story.story_id)
        if sw is None:
            logger.error('Story %s not found in state', story.story_id)
            return False
        step = sw.find_step(step_id)
        if step is None:
            logger.error('Step %s not found in story %s', step_id, story.story_id)
            return False

        # Mark step as in_progress
        step.status = StepStatus.in_progress
        step.started_at = _now_iso()

        # Capture git SHA
        try:
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                capture_output=True,
                text=True,
                check=True,
            )
            step.git_sha_at_start = result.stdout.strip()
        except subprocess.CalledProcessError:
            step.git_sha_at_start = None

        _add_history(sw, 'step_started', agent_id, step.id)

    # Compose the prompt (outside the lock)
    global_scratch = read_global_scratch(shared_dir)
    story_scratch = read_story_scratch(story.story_id, shared_dir)

    step_obj = story.find_step(step_id)
    if step_obj is None:
        return False

    prompt = compose_step_prompt(
        story=story,
        step=step_obj,
        global_scratch=global_scratch,
        story_scratch=story_scratch,
        base_instructions=BASE_AGENT_INSTRUCTIONS,
    )

    # Determine timeout for this step type
    timeout_secs = STEP_TIMEOUTS.get(step_obj.type, 900)
    effective_max_turns = max_turns

    _print_progress(
        f'  [{story.story_id}] Step {step_id} ({step_obj.type}) starting (timeout={timeout_secs}s)', run_dir=run_dir
    )

    # Launch the agent
    start_time = time.monotonic()
    returncode, result_info = _run_agent_docker(
        task=prompt,
        agent_id=agent_id,
        max_turns=effective_max_turns,
    )
    elapsed = time.monotonic() - start_time

    # Process any workflow edit requests
    edits_applied = False
    try:
        edits = parse_edit_file(story.story_id, shared_dir)
        if edits is not None:
            # Reload state for edit application
            with locked_state(state_path) as state:
                sw = state.stories[story.story_id]
                try:
                    validate_edits(sw, edits)
                    apply_edits(sw, edits)
                    remove_edit_file(story.story_id, shared_dir)
                    edits_applied = True
                    _add_history(sw, 'workflow_edit', agent_id, step_id, edits_count=len(edits))
                    logger.info(
                        'Applied %d workflow edits for story %s',
                        len(edits),
                        story.story_id,
                    )
                except EditValidationError as exc:
                    logger.warning(
                        'Workflow edits failed validation for story %s: %s',
                        story.story_id,
                        exc,
                    )
                    discard_edit_file(story.story_id, shared_dir)
    except Exception:
        logger.exception('Error processing workflow edits for story %s', story.story_id)
        discard_edit_file(story.story_id, shared_dir)

    # Extract summary from agent output for inter-step context
    last_text = result_info.get('last_assistant_text', '')
    summary = _extract_summary(last_text) if last_text else None

    # Update step status in state
    success = returncode == 0
    with locked_state(state_path) as state:
        sw = state.stories[story.story_id]
        step = sw.find_step(step_id)
        if step is None:
            # Step may have been replaced by edits (e.g., split). If edits were
            # applied, treat the original step as handled.
            if edits_applied:
                return True
            return False

        step.completed_at = _now_iso()
        step.cost_usd = result_info.get('cost_usd')
        step.input_tokens = result_info.get('input_tokens')
        step.output_tokens = result_info.get('output_tokens')
        step.notes = summary

        if success:
            step.status = StepStatus.completed
            _add_history(sw, 'step_completed', agent_id, step.id, elapsed_secs=round(elapsed, 1))
        else:
            step.status = StepStatus.failed
            step.error = f'Agent exited with code {returncode}'
            _add_history(sw, 'step_failed', agent_id, step.id, returncode=returncode, elapsed_secs=round(elapsed, 1))
            # Save diff and reset git on failure (per spec)
            if step.git_sha_at_start:
                diff_path = shared_dir / 'logs' / story.story_id / f'{step_id}.diff'
                _save_diff_and_reset(diff_path, step.git_sha_at_start)

    status_label = 'completed' if success else 'FAILED'
    _print_progress(
        f'  [{story.story_id}] Step {step_id} ({step_obj.type}) {status_label} in {elapsed:.0f}s', run_dir=run_dir
    )

    return success


# ---------------------------------------------------------------------------
# Story execution
# ---------------------------------------------------------------------------


def run_story_steps(
    story_id: str,
    agent_id: int,
    state_path: Path,
    shared_dir: Path,
    max_turns: int | None = None,
    run_dir: Path | None = None,
) -> bool:
    """Execute all steps for a single story in sequence.

    Returns True if the story completed all steps, False if any step failed.
    """
    while True:
        # Reload state each iteration to pick up any edits
        state = load_state(state_path)
        story = state.stories.get(story_id)
        if story is None:
            logger.error('Story %s disappeared from state', story_id)
            return False

        # Find next pending step
        step = story.find_next_pending_step()
        if step is None:
            # All steps done (completed or skipped) -- story is finished
            _print_progress(f'  [{story_id}] All steps completed', run_dir=run_dir)
            return True

        # Execute the step
        success = execute_step(
            story=story,
            step_id=step.id,
            agent_id=agent_id,
            state_path=state_path,
            shared_dir=shared_dir,
            max_turns=max_turns,
            run_dir=run_dir,
        )

        if not success:
            # Check if step was replaced by edits
            refreshed = load_state(state_path)
            refreshed_story = refreshed.stories.get(story_id)
            if refreshed_story:
                refreshed_step = refreshed_story.find_step(step.id)
                if refreshed_step is None:
                    # Step was replaced by edits, continue to next iteration
                    continue
                if refreshed_step.status == StepStatus.failed:
                    # Genuine failure -- mark story as failed
                    with locked_state(state_path) as s:
                        sw = s.stories[story_id]
                        sw.status = StoryStatus.failed
                        sw.completed_at = _now_iso()
                        _add_history(sw, 'story_failed', agent_id, step.id)

                    append_global_scratch(
                        f'[{_now_iso()}] Story {story_id} FAILED at step {step.id} ({step.type})',
                        shared_dir,
                    )
                    return False
            else:
                return False

    # unreachable, but satisfies type checker
    return False  # pragma: no cover


# ---------------------------------------------------------------------------
# Mark dependent stories as blocked
# ---------------------------------------------------------------------------


def _block_dependents(state_path: Path, failed_story_id: str) -> None:
    """Mark stories that depend (directly or transitively) on a failed story as blocked."""
    with locked_state(state_path) as state:
        # Collect all failed/blocked story IDs (including the newly failed one)
        failed_ids: set[str] = {
            sid for sid, s in state.stories.items() if s.status in (StoryStatus.failed, StoryStatus.blocked)
        }
        failed_ids.add(failed_story_id)

        # Iteratively propagate blocking until no new stories are blocked
        changed = True
        while changed:
            changed = False
            for story in state.stories.values():
                if story.status != StoryStatus.unclaimed:
                    continue
                if any(dep in failed_ids for dep in story.depends_on):
                    story.status = StoryStatus.blocked
                    story.history.append(
                        HistoryEntry(
                            timestamp=_now_iso(),
                            action='story_failed',
                            details={'reason': f'dependency {failed_story_id} failed (transitive)'},
                        )
                    )
                    failed_ids.add(story.story_id)
                    changed = True
                    logger.warning(
                        'Story %s blocked: depends on failed story %s',
                        story.story_id,
                        failed_story_id,
                    )


# ---------------------------------------------------------------------------
# Re-evaluate blocked stories
# ---------------------------------------------------------------------------


def _reevaluate_blocked_stories(state_path: Path) -> None:
    """Transition blocked stories back to unclaimed if all their deps are completed."""
    with locked_state(state_path) as state:
        completed_ids: set[str] = {sid for sid, s in state.stories.items() if s.status == StoryStatus.completed}
        for story in state.stories.values():
            if story.status != StoryStatus.blocked:
                continue
            if all(dep in completed_ids for dep in story.depends_on):
                story.status = StoryStatus.unclaimed
                logger.info(
                    'Story %s unblocked: all dependencies now completed',
                    story.story_id,
                )


# ---------------------------------------------------------------------------
# Print status summary
# ---------------------------------------------------------------------------


def _print_status_summary(state_path: Path, run_dir: Path | None = None) -> None:
    """Print a summary of all story statuses."""
    state = load_state(state_path)
    counts: dict[str, int] = {}
    for story in state.stories.values():
        counts[story.status] = counts.get(story.status, 0) + 1

    parts = [f'{status}={count}' for status, count in sorted(counts.items())]
    total = len(state.stories)
    _print_progress(f'  Status: {total} stories — {", ".join(parts)}', run_dir=run_dir)


# ---------------------------------------------------------------------------
# Mode 1: One-shot
# ---------------------------------------------------------------------------


def run_one_shot(
    task: str, agent_id: int, max_turns: int | None, shared_dir: Path, state_path: Path, run_dir: Path | None = None
) -> int:
    """Run a single task through the full step-based workflow.

    Uses the provided run directory for state. State persists after completion.
    Returns 0 on success, 1 on failure.
    """
    # Create a single story with the default workflow
    story = StoryWorkflow(
        story_id='oneshot',
        title=task[:80],
        description=task,
        status=StoryStatus.in_progress,
        agent_id=agent_id,
        claimed_at=_now_iso(),
        steps=create_default_workflow(),
    )

    state = WorkflowState(
        version=1,
        created_at=_now_iso(),
        prd_file='',
        stories={'oneshot': story},
    )
    save_state(state, state_path)

    _print_progress(f'One-shot mode: executing task with {len(story.steps)} steps', run_dir=run_dir)
    _print_progress(f'  State: {state_path}', run_dir=run_dir)

    success = run_story_steps(
        story_id='oneshot',
        agent_id=agent_id,
        state_path=state_path,
        shared_dir=shared_dir,
        max_turns=max_turns,
        run_dir=run_dir,
    )

    if success:
        _print_progress('One-shot task completed successfully.', run_dir=run_dir)
        return 0
    else:
        _print_progress('One-shot task FAILED.', run_dir=run_dir)
        return 1


# ---------------------------------------------------------------------------
# Mode 2: PRD serial
# ---------------------------------------------------------------------------


def run_serial(
    prd_path: Path,
    agent_id: int,
    max_iterations: int,
    state_path: Path,
    shared_dir: Path,
    resume: bool,
    max_turns: int | None,
    run_dir: Path | None = None,
) -> None:
    """Run stories from a PRD file serially, one at a time."""
    # Initialize state from PRD if needed
    if state_path.exists() and resume:
        _print_progress(f'Resuming from existing state: {state_path}', run_dir=run_dir)
    elif state_path.exists() and not resume:
        _print_progress(f'Re-initializing state from PRD (overwriting {state_path})', run_dir=run_dir)
        initialize_state_from_prd(prd_path, state_path)
    else:
        _print_progress(f'Initializing state from PRD: {prd_path}', run_dir=run_dir)
        initialize_state_from_prd(prd_path, state_path)

    for iteration in range(1, max_iterations + 1):
        # Re-evaluate blocked stories each iteration (per spec)
        _reevaluate_blocked_stories(state_path)

        # Find an assignable story
        with locked_state(state_path) as state:
            story = find_assignable_story(state)
            if story is None:
                # Check if there are any in_progress or unclaimed stories remaining
                remaining = [
                    s for s in state.stories.values() if s.status in (StoryStatus.unclaimed, StoryStatus.in_progress)
                ]
                if not remaining:
                    _print_progress(f'\nAll stories finished after {iteration - 1} iterations.', run_dir=run_dir)
                    break
                else:
                    # Stories exist but none are assignable (blocked by deps)
                    _print_progress(
                        f'\nNo assignable stories. {len(remaining)} stories remain but are blocked by dependencies.',
                        run_dir=run_dir,
                    )
                    break

            # Claim the story
            story.status = StoryStatus.in_progress
            story.agent_id = agent_id
            story.claimed_at = _now_iso()

            # Populate steps if empty
            if not story.steps:
                story.steps = create_default_workflow()

            _add_history(story, 'story_claimed', agent_id)
            story_id = story.story_id
            story_title = story.title

        print(f'\n{"=" * 60}', flush=True)
        _print_progress(f'Iteration {iteration}/{max_iterations}: [{story_id}] {story_title}', run_dir=run_dir)
        print(f'{"=" * 60}\n', flush=True)

        # Run all steps
        success = run_story_steps(
            story_id=story_id,
            agent_id=agent_id,
            state_path=state_path,
            shared_dir=shared_dir,
            max_turns=max_turns,
            run_dir=run_dir,
        )

        if success:
            with locked_state(state_path) as state:
                sw = state.stories[story_id]
                sw.status = StoryStatus.completed
                sw.completed_at = _now_iso()
                _add_history(sw, 'story_completed', agent_id)

            cleanup_story_scratch(story_id, shared_dir)
            _print_progress(f'  Story {story_id} completed successfully.', run_dir=run_dir)
        else:
            # Story already marked failed in run_story_steps
            _block_dependents(state_path, story_id)
            _print_progress(f'  Story {story_id} FAILED.', run_dir=run_dir)

        _print_status_summary(state_path, run_dir=run_dir)
    else:
        _print_progress(f'\nMax iterations ({max_iterations}) reached.', run_dir=run_dir)


# ---------------------------------------------------------------------------
# Mode 3: PRD parallel
# ---------------------------------------------------------------------------


def _create_worktree(agent_id: int, story_id: str) -> Path:
    """Create a git worktree for an agent working on a specific story."""
    worktree_path = WORKTREE_DIR / f'agent-{agent_id}'
    branch_name = f'ralph/{story_id}'

    WORKTREE_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up stale worktree entries then remove this worktree if registered
    subprocess.run(['git', 'worktree', 'prune'], capture_output=True)
    subprocess.run(
        ['git', 'worktree', 'remove', '--force', str(worktree_path)],
        capture_output=True,
    )

    # Remove leftover directory (git worktree remove may have failed if already pruned)
    if worktree_path.exists():
        shutil.rmtree(worktree_path)

    # Delete branch if it exists from a previous run
    subprocess.run(
        ['git', 'branch', '-D', branch_name],
        capture_output=True,
    )

    subprocess.run(
        ['git', 'worktree', 'add', str(worktree_path), '-b', branch_name, _git_main_branch()],
        check=True,
        capture_output=True,
        text=True,
    )

    logger.info('Created worktree %s on branch %s', worktree_path, branch_name)
    return worktree_path


def _remove_worktree(agent_id: int) -> None:
    """Remove a git worktree for an agent."""
    worktree_path = WORKTREE_DIR / f'agent-{agent_id}'
    if worktree_path.exists():
        subprocess.run(
            ['git', 'worktree', 'remove', '--force', str(worktree_path)],
            capture_output=True,
        )
        logger.info('Removed worktree %s', worktree_path)


def _merge_worktree(agent_id: int, story_id: str) -> bool:
    """Rebase worktree branch onto the main branch and squash merge.

    Returns True on success, False on merge failure.
    """
    branch_name = f'ralph/{story_id}'
    worktree_path = WORKTREE_DIR / f'agent-{agent_id}'
    main_branch = _git_main_branch()

    # Rebase onto main branch
    result = subprocess.run(
        ['git', '-C', str(worktree_path), 'rebase', main_branch],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(
            'Rebase failed for story %s:\n%s',
            story_id,
            result.stderr,
        )
        subprocess.run(
            ['git', '-C', str(worktree_path), 'rebase', '--abort'],
            capture_output=True,
        )
        return False

    # Squash merge into main branch
    result = subprocess.run(
        ['git', 'merge', '--squash', branch_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(
            'Squash merge failed for story %s:\n%s',
            story_id,
            result.stderr,
        )
        subprocess.run(['git', 'reset', '--hard', 'HEAD'], capture_output=True)
        return False

    # Commit the squash merge
    result = subprocess.run(
        ['git', 'commit', '-m', f'feat: {story_id} (squash merge from ralph/{story_id})'],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error('Commit failed for story %s:\n%s', story_id, result.stderr)
        return False

    logger.info('Merged story %s into %s', story_id, main_branch)
    return True


def run_parallel(
    prd_path: Path,
    num_agents: int,
    max_iterations: int,
    state_path: Path,
    shared_dir: Path,
    resume: bool,
    max_turns: int | None,
    run_dir: Path | None = None,
) -> None:
    """Run stories from a PRD file in parallel with multiple agents.

    Each agent gets its own git worktree. Stories are assigned and executed
    concurrently. Completed stories are rebased and squash-merged into the main branch.
    """
    # Initialize state
    if state_path.exists() and resume:
        _print_progress(f'Resuming from existing state: {state_path}', run_dir=run_dir)
    elif state_path.exists() and not resume:
        _print_progress(f'Re-initializing state from PRD (overwriting {state_path})', run_dir=run_dir)
        initialize_state_from_prd(prd_path, state_path)
    else:
        _print_progress(f'Initializing state from PRD: {prd_path}', run_dir=run_dir)
        initialize_state_from_prd(prd_path, state_path)

    # Track active agent subprocesses
    # Maps agent_id -> (process, story_id, worktree_path)
    active: dict[int, tuple[subprocess.Popen, str, Path]] = {}
    # Pool of available agent IDs
    available_agents: list[int] = list(range(1, num_agents + 1))

    _print_progress(f'Parallel mode: {num_agents} agents', run_dir=run_dir)

    iterations = 0

    try:
        while iterations < max_iterations:
            # Re-evaluate blocked stories each iteration (per spec)
            _reevaluate_blocked_stories(state_path)

            # Try to assign stories to available agents
            while available_agents:
                # Claim a story inside the lock
                assigned_aid: int | None = None
                assigned_story_id: str | None = None
                assigned_story_title: str | None = None

                with locked_state(state_path) as state:
                    story = find_assignable_story(state)
                    if story is not None:
                        assigned_aid = available_agents.pop(0)
                        story.status = StoryStatus.in_progress
                        story.agent_id = assigned_aid
                        story.claimed_at = _now_iso()
                        if not story.steps:
                            story.steps = create_default_workflow()
                        _add_history(story, 'story_claimed', assigned_aid)
                        assigned_story_id = story.story_id
                        assigned_story_title = story.title

                if assigned_aid is None or assigned_story_id is None:
                    # No assignable story found
                    break

                # Spawn agent outside the lock
                _print_progress(
                    f'  Agent {assigned_aid}: starting story [{assigned_story_id}] {assigned_story_title}',
                    run_dir=run_dir,
                )

                worktree_path = _create_worktree(assigned_aid, assigned_story_id)

                # Launch this script in single-story mode as a subprocess
                cmd = [
                    sys.executable,
                    __file__,
                    '--story-id',
                    assigned_story_id,
                    '--agent-id',
                    str(assigned_aid),
                    '--state-path',
                    str(state_path.resolve()),
                    '--shared-dir',
                    str(shared_dir.resolve()),
                ]
                if max_turns is not None:
                    cmd.extend(['--max-turns', str(max_turns)])

                proc = subprocess.Popen(
                    cmd,
                    cwd=str(worktree_path),
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
                active[assigned_aid] = (proc, assigned_story_id, worktree_path)
                iterations += 1

            if not active:
                # No active agents and no stories to assign
                state = load_state(state_path)
                remaining = [
                    s for s in state.stories.values() if s.status in (StoryStatus.unclaimed, StoryStatus.in_progress)
                ]
                if not remaining:
                    _print_progress('\nAll stories finished (parallel mode).', run_dir=run_dir)
                else:
                    _print_progress(
                        f'\nNo assignable stories. {len(remaining)} stories remain but are blocked or in progress.',
                        run_dir=run_dir,
                    )
                break

            # Wait for any agent to finish
            while active:
                for aid, (proc, story_id, worktree_path) in list(active.items()):
                    ret = proc.poll()
                    if ret is not None:
                        # Agent finished
                        del active[aid]
                        available_agents.append(aid)
                        available_agents.sort()

                        if ret == 0:
                            _print_progress(f'  Agent {aid}: story [{story_id}] completed', run_dir=run_dir)

                            # Merge worktree into main branch
                            if _merge_worktree(aid, story_id):
                                _print_progress(f'  Agent {aid}: merged [{story_id}] into main branch', run_dir=run_dir)
                            else:
                                _print_progress(f'  Agent {aid}: merge FAILED for [{story_id}]', run_dir=run_dir)
                        else:
                            _print_progress(f'  Agent {aid}: story [{story_id}] FAILED (exit {ret})', run_dir=run_dir)
                            _block_dependents(state_path, story_id)

                        # Cleanup worktree
                        _remove_worktree(aid)

                        # Break out to try assigning new stories
                        break
                else:
                    # No agent finished yet, wait a bit
                    time.sleep(2)
                    continue
                # An agent finished, go back to the assignment loop
                break

            _print_status_summary(state_path, run_dir=run_dir)

    finally:
        # Cleanup any remaining active agents
        for aid, (proc, story_id, _) in active.items():
            logger.warning('Terminating agent %d (story %s)', aid, story_id)
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            _remove_worktree(aid)

    _print_status_summary(state_path, run_dir=run_dir)


# ---------------------------------------------------------------------------
# Single-story mode (used internally by parallel mode)
# ---------------------------------------------------------------------------


def run_single_story(
    story_id: str,
    agent_id: int,
    state_path: Path,
    shared_dir: Path,
    max_turns: int | None,
    run_dir: Path | None = None,
) -> None:
    """Execute a single story (entry point for subprocess in parallel mode).

    Claims the story (if not already claimed), runs all steps, marks
    complete or failed.
    """
    # Ensure the story is claimed
    with locked_state(state_path) as state:
        story = state.stories.get(story_id)
        if story is None:
            logger.error('Story %s not found in state file', story_id)
            sys.exit(1)

        if story.status == StoryStatus.unclaimed:
            story.status = StoryStatus.in_progress
            story.agent_id = agent_id
            story.claimed_at = _now_iso()
            if not story.steps:
                story.steps = create_default_workflow()
            _add_history(story, 'story_claimed', agent_id)

    _print_progress(f'Agent {agent_id}: running story [{story_id}]', run_dir=run_dir)

    success = run_story_steps(
        story_id=story_id,
        agent_id=agent_id,
        state_path=state_path,
        shared_dir=shared_dir,
        max_turns=max_turns,
        run_dir=run_dir,
    )

    if success:
        with locked_state(state_path) as state:
            sw = state.stories[story_id]
            sw.status = StoryStatus.completed
            sw.completed_at = _now_iso()
            _add_history(sw, 'story_completed', agent_id)

        cleanup_story_scratch(story_id, shared_dir)
        _print_progress(f'Agent {agent_id}: story [{story_id}] completed successfully.', run_dir=run_dir)
        sys.exit(0)
    else:
        _print_progress(f'Agent {agent_id}: story [{story_id}] FAILED.', run_dir=run_dir)
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Dynamic Ralph orchestrator: step-based workflow execution.',
    )

    parser.add_argument(
        'task',
        nargs='?',
        default=None,
        help='One-shot mode: task description (positional arg)',
    )
    parser.add_argument(
        '--prd',
        type=Path,
        default=None,
        help='PRD file for multi-story mode',
    )
    parser.add_argument(
        '--agents',
        type=int,
        default=1,
        help='Number of parallel agents (default: 1 = serial)',
    )
    parser.add_argument(
        '--agent-id',
        type=int,
        default=1,
        help='Agent ID (default: 1)',
    )
    parser.add_argument(
        '--story-id',
        type=str,
        default=None,
        help='Run a specific story (used internally by parallel mode)',
    )
    parser.add_argument(
        '--state-path',
        type=Path,
        default=None,
        help='Custom state file path (default: <run_dir>/workflow_state.json)',
    )
    parser.add_argument(
        '--shared-dir',
        type=Path,
        default=None,
        help='Shared directory for scratch files (default: auto-generated run directory)',
    )
    parser.add_argument(
        '--max-iterations',
        type=int,
        default=50,
        help='Max story iterations (default: 50)',
    )
    parser.add_argument(
        '--max-turns',
        type=int,
        default=None,
        help='Max turns per step',
    )
    parser.add_argument(
        '--build',
        action='store_true',
        help='Rebuild Docker image',
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume from existing workflow_state.json instead of reinitializing',
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        stream=sys.stderr,
    )

    # Build Docker image if requested or missing
    if args.build or not image_exists():
        build_image()

    # Auto-generate run directory when --shared-dir is not explicitly provided
    if args.shared_dir is None:
        run_dir = generate_run_dir()
        args.shared_dir = run_dir
        if args.state_path is None:
            args.state_path = run_dir / 'workflow_state.json'
        _print_progress(f'Run directory: {run_dir}', run_dir=run_dir)
    else:
        # Explicit --shared-dir provided; use it as run_dir for summary.log
        run_dir = args.shared_dir
        if args.state_path is None:
            args.state_path = args.shared_dir / 'workflow_state.json'

    # Dispatch to the appropriate mode
    if args.story_id is not None:
        # Single-story mode (used by parallel spawner)
        run_single_story(
            story_id=args.story_id,
            agent_id=args.agent_id,
            state_path=args.state_path,
            shared_dir=args.shared_dir,
            max_turns=args.max_turns,
            run_dir=run_dir,
        )

    elif args.task is not None and args.prd is None:
        # One-shot mode
        rc = run_one_shot(
            task=args.task,
            agent_id=args.agent_id,
            max_turns=args.max_turns,
            shared_dir=args.shared_dir,
            state_path=args.state_path,
            run_dir=run_dir,
        )
        sys.exit(rc)

    elif args.prd is not None:
        # PRD mode
        if not args.prd.exists():
            print(f'Error: PRD file not found: {args.prd}', file=sys.stderr)
            sys.exit(1)

        if args.agents > 1:
            # Parallel mode
            run_parallel(
                prd_path=args.prd,
                num_agents=args.agents,
                max_iterations=args.max_iterations,
                state_path=args.state_path,
                shared_dir=args.shared_dir,
                resume=args.resume,
                max_turns=args.max_turns,
                run_dir=run_dir,
            )
        else:
            # Serial mode
            run_serial(
                prd_path=args.prd,
                agent_id=args.agent_id,
                max_iterations=args.max_iterations,
                state_path=args.state_path,
                shared_dir=args.shared_dir,
                resume=args.resume,
                max_turns=args.max_turns,
                run_dir=run_dir,
            )

    else:
        parser.error('Provide a task (positional) for one-shot mode or --prd for PRD mode.')


if __name__ == '__main__':
    main()
