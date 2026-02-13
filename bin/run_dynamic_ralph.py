#!/usr/bin/env python3
"""Dynamic Ralph orchestrator: step-based workflow execution.

Supports three modes:
  1. One-shot: single task, persistent state in run directory, full 10-step workflow.
  2. PRD serial: pick stories from prd.json, execute steps one at a time.
  3. PRD parallel: spawn up to N agents via git worktrees for concurrent stories.
"""

import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from multi_agent import (
    build_image,
    image_exists,
)
from multi_agent.workflow.executor import _now_iso, execute_step
from multi_agent.workflow.models import (
    HistoryEntry,
    StepStatus,
    StoryStatus,
    StoryWorkflow,
    WorkflowState,
)
from multi_agent.workflow.scratch import (
    append_global_scratch,
    cleanup_story_scratch,
)
from multi_agent.workflow.state import (
    find_assignable_story,
    initialize_state_from_prd,
    load_state,
    locked_state,
    save_state,
)
from multi_agent.workflow.steps import create_default_workflow


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


def _print_progress(message: str, shared_dir: Path | None = None) -> None:
    """Print progress to stdout and log to stderr."""
    print(message, flush=True)
    logger.info(message)
    if shared_dir is not None:
        append_summary(message, shared_dir)


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


def _write_metadata(shared_dir: Path) -> None:
    """Write metadata.json with environment info for post-run analysis."""
    git_branch = subprocess.run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        capture_output=True,
        text=True,
    ).stdout.strip()

    git_sha = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        capture_output=True,
        text=True,
    ).stdout.strip()

    ralph_env_vars = {k: v for k, v in os.environ.items() if k.startswith('RALPH_')}

    metadata = {
        'timestamp': datetime.now(UTC).isoformat(),
        'hostname': socket.gethostname(),
        'python_version': sys.version,
        'git_branch': git_branch,
        'git_sha': git_sha,
        'ralph_image': os.environ.get('RALPH_IMAGE', ''),
        'ralph_env_vars': ralph_env_vars,
    }

    metadata_path = shared_dir / 'metadata.json'
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')


# ---------------------------------------------------------------------------
# Story execution
# ---------------------------------------------------------------------------


def run_story_steps(
    story_id: str,
    agent_id: int,
    state_path: Path,
    shared_dir: Path,
    max_turns: int | None = None,
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
            _print_progress(f'  [{story_id}] All steps completed', shared_dir=shared_dir)
            return True

        # Execute the step via the unified executor
        result_step = execute_step(
            story=story,
            step=step,
            agent_id=agent_id,
            state_path=state_path,
            shared_dir=shared_dir,
            max_turns=max_turns,
            on_progress=_print_progress,
        )

        if result_step.status == StepStatus.completed:
            continue

        if result_step.status == StepStatus.cancelled:
            # Timeout — treat as failure for the story
            with locked_state(state_path) as s:
                sw = s.stories[story_id]
                sw.status = StoryStatus.failed
                sw.completed_at = _now_iso()
                _add_history(sw, 'story_failed', agent_id, step.id)

            append_global_scratch(
                f'[{_now_iso()}] Story {story_id} FAILED at step {step.id} ({step.type}) — timed out',
                shared_dir,
            )
            return False

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


def _print_status_summary(state_path: Path, shared_dir: Path | None = None) -> None:
    """Print a summary of all story statuses."""
    state = load_state(state_path)
    counts: dict[str, int] = {}
    for story in state.stories.values():
        counts[story.status] = counts.get(story.status, 0) + 1

    parts = [f'{status}={count}' for status, count in sorted(counts.items())]
    total = len(state.stories)
    _print_progress(f'  Status: {total} stories — {", ".join(parts)}', shared_dir=shared_dir)


# ---------------------------------------------------------------------------
# Mode 1: One-shot
# ---------------------------------------------------------------------------


def run_one_shot(task: str, agent_id: int, max_turns: int | None, shared_dir: Path, state_path: Path) -> int:
    """Run a single task through the full step-based workflow.

    *shared_dir* is the directory used for logs, scratch files, and workflow
    edit files.  *state_path* is the JSON file where workflow state is persisted
    across steps.  State persists after completion.

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

    _print_progress(f'One-shot mode: executing task with {len(story.steps)} steps', shared_dir=shared_dir)
    _print_progress(f'  State: {state_path}', shared_dir=shared_dir)

    success = run_story_steps(
        story_id='oneshot',
        agent_id=agent_id,
        state_path=state_path,
        shared_dir=shared_dir,
        max_turns=max_turns,
    )

    if success:
        _print_progress('One-shot task completed successfully.', shared_dir=shared_dir)
        return 0
    else:
        _print_progress('One-shot task FAILED.', shared_dir=shared_dir)
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
) -> None:
    """Run stories from a PRD file serially, one at a time.

    *state_path* is the JSON file for persisted workflow state.  *shared_dir* is
    the directory for logs, scratch files, and workflow edits.  When *resume* is
    True, existing state is reused instead of reinitializing from the PRD.
    *max_turns* optionally limits the number of agent turns per step.
    """
    # Initialize state from PRD if needed
    if state_path.exists() and resume:
        _print_progress(f'Resuming from existing state: {state_path}', shared_dir=shared_dir)
    elif state_path.exists() and not resume:
        _print_progress(f'Re-initializing state from PRD (overwriting {state_path})', shared_dir=shared_dir)
        initialize_state_from_prd(prd_path, state_path)
    else:
        _print_progress(f'Initializing state from PRD: {prd_path}', shared_dir=shared_dir)
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
                    _print_progress(f'\nAll stories finished after {iteration - 1} iterations.', shared_dir=shared_dir)
                    with locked_state(state_path) as s:
                        s.finished_at = _now_iso()
                    break
                else:
                    # Stories exist but none are assignable (blocked by deps)
                    _print_progress(
                        f'\nNo assignable stories. {len(remaining)} stories remain but are blocked by dependencies.',
                        shared_dir=shared_dir,
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
        _print_progress(f'Iteration {iteration}/{max_iterations}: [{story_id}] {story_title}', shared_dir=shared_dir)
        print(f'{"=" * 60}\n', flush=True)

        # Run all steps
        success = run_story_steps(
            story_id=story_id,
            agent_id=agent_id,
            state_path=state_path,
            shared_dir=shared_dir,
            max_turns=max_turns,
        )

        if success:
            with locked_state(state_path) as state:
                sw = state.stories[story_id]
                sw.status = StoryStatus.completed
                sw.completed_at = _now_iso()
                _add_history(sw, 'story_completed', agent_id)

            cleanup_story_scratch(story_id, shared_dir)
            _print_progress(f'  Story {story_id} completed successfully.', shared_dir=shared_dir)
        else:
            # Story already marked failed in run_story_steps
            _block_dependents(state_path, story_id)
            _print_progress(f'  Story {story_id} FAILED.', shared_dir=shared_dir)

        _print_status_summary(state_path, shared_dir=shared_dir)
    else:
        _print_progress(f'\nMax iterations ({max_iterations}) reached.', shared_dir=shared_dir)


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
        ['git', 'commit', '-m', f'{story_id} (squash merge from ralph/{story_id})'],
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
) -> None:
    """Run stories from a PRD file in parallel with multiple agents.

    Each agent gets its own git worktree. Stories are assigned and executed
    concurrently. Completed stories are rebased and squash-merged into the main
    branch.

    *state_path* is the JSON file for persisted workflow state.  *shared_dir* is
    the directory for logs, scratch files, and workflow edits.  When *resume* is
    True, existing state is reused instead of reinitializing from the PRD.
    *max_turns* optionally limits the number of agent turns per step.
    """
    # Initialize state
    if state_path.exists() and resume:
        _print_progress(f'Resuming from existing state: {state_path}', shared_dir=shared_dir)
    elif state_path.exists() and not resume:
        _print_progress(f'Re-initializing state from PRD (overwriting {state_path})', shared_dir=shared_dir)
        initialize_state_from_prd(prd_path, state_path)
    else:
        _print_progress(f'Initializing state from PRD: {prd_path}', shared_dir=shared_dir)
        initialize_state_from_prd(prd_path, state_path)

    # Track active agent subprocesses
    # Maps agent_id -> (process, story_id, worktree_path)
    active: dict[int, tuple[subprocess.Popen, str, Path]] = {}
    # Pool of available agent IDs
    available_agents: list[int] = list(range(1, num_agents + 1))

    _print_progress(f'Parallel mode: {num_agents} agents', shared_dir=shared_dir)

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
                    shared_dir=shared_dir,
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
                    _print_progress('\nAll stories finished (parallel mode).', shared_dir=shared_dir)
                    with locked_state(state_path) as s:
                        s.finished_at = _now_iso()
                else:
                    _print_progress(
                        f'\nNo assignable stories. {len(remaining)} stories remain but are blocked or in progress.',
                        shared_dir=shared_dir,
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
                            _print_progress(f'  Agent {aid}: story [{story_id}] completed', shared_dir=shared_dir)

                            # Merge worktree into main branch
                            if _merge_worktree(aid, story_id):
                                _print_progress(
                                    f'  Agent {aid}: merged [{story_id}] into main branch', shared_dir=shared_dir
                                )
                            else:
                                _print_progress(f'  Agent {aid}: merge FAILED for [{story_id}]', shared_dir=shared_dir)
                        else:
                            _print_progress(
                                f'  Agent {aid}: story [{story_id}] FAILED (exit {ret})', shared_dir=shared_dir
                            )
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

            _print_status_summary(state_path, shared_dir=shared_dir)

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

    _print_status_summary(state_path, shared_dir=shared_dir)


# ---------------------------------------------------------------------------
# Single-story mode (used internally by parallel mode)
# ---------------------------------------------------------------------------


def run_single_story(
    story_id: str,
    agent_id: int,
    state_path: Path,
    shared_dir: Path,
    max_turns: int | None,
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

    _print_progress(f'Agent {agent_id}: running story [{story_id}]', shared_dir=shared_dir)

    success = run_story_steps(
        story_id=story_id,
        agent_id=agent_id,
        state_path=state_path,
        shared_dir=shared_dir,
        max_turns=max_turns,
    )

    if success:
        with locked_state(state_path) as state:
            sw = state.stories[story_id]
            sw.status = StoryStatus.completed
            sw.completed_at = _now_iso()
            _add_history(sw, 'story_completed', agent_id)

        cleanup_story_scratch(story_id, shared_dir)
        _print_progress(f'Agent {agent_id}: story [{story_id}] completed successfully.', shared_dir=shared_dir)
        sys.exit(0)
    else:
        _print_progress(f'Agent {agent_id}: story [{story_id}] FAILED.', shared_dir=shared_dir)
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
        help='Custom state file path (default: <shared_dir>/workflow_state.json)',
    )
    parser.add_argument(
        '--shared-dir',
        type=Path,
        default=None,
        help='Shared directory for logs, scratch files, and state (default: auto-generated under run_ralph/)',
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
        args.shared_dir = generate_run_dir()
        if args.state_path is None:
            args.state_path = args.shared_dir / 'workflow_state.json'
        _print_progress(f'Run directory: {args.shared_dir}', shared_dir=args.shared_dir)
    else:
        # Explicit --shared-dir provided
        if args.state_path is None:
            args.state_path = args.shared_dir / 'workflow_state.json'

    # Write metadata and copy PRD at startup
    _write_metadata(args.shared_dir)
    if args.prd is not None and args.prd.exists():
        shutil.copy2(args.prd, args.shared_dir / 'prd.json')

    # Dispatch to the appropriate mode
    exit_status = 'success'
    try:
        if args.story_id is not None:
            # Single-story mode (used by parallel spawner)
            run_single_story(
                story_id=args.story_id,
                agent_id=args.agent_id,
                state_path=args.state_path,
                shared_dir=args.shared_dir,
                max_turns=args.max_turns,
            )

        elif args.task is not None and args.prd is None:
            # One-shot mode
            rc = run_one_shot(
                task=args.task,
                agent_id=args.agent_id,
                max_turns=args.max_turns,
                shared_dir=args.shared_dir,
                state_path=args.state_path,
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
                )

        else:
            parser.error('Provide a task (positional) for one-shot mode or --prd for PRD mode.')
    except KeyboardInterrupt:
        exit_status = 'interrupted'
        raise
    except SystemExit:
        raise
    except Exception:
        exit_status = 'failure'
        raise
    finally:
        append_summary(f'Run finished: {exit_status}', args.shared_dir)


if __name__ == '__main__':
    main()
