"""Workflow state file I/O with FileLock for concurrent access."""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from multi_agent.filelock import FileLock
from multi_agent.workflow.models import StoryStatus, StoryWorkflow, WorkflowState


LOCK_TIMEOUT: int = 60


def load_state(state_path: Path) -> WorkflowState:
    """Load workflow state from a JSON file, parsing it with Pydantic."""
    text = state_path.read_text(encoding='utf-8')
    data = json.loads(text)
    return WorkflowState.model_validate(data)


def save_state(state: WorkflowState, state_path: Path) -> None:
    """Write state atomically: write to a temp file in the same directory, then rename."""
    content = json.dumps(state.model_dump(), indent=2)
    parent = state_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix='.tmp', prefix='.state_')
    try:
        with open(fd, 'w', encoding='utf-8') as f:
            f.write(content)
            f.write('\n')
        Path(tmp_path).rename(state_path)
    except BaseException:
        # Clean up the temp file on any failure
        Path(tmp_path).unlink(missing_ok=True)
        raise


@contextmanager
def locked_state(state_path: Path):
    """Context manager: acquire FileLock, load state, yield it, save on exit.

    Usage::

        with locked_state() as state:
            # modify state
            ...
        # automatically saved on exit
    """
    lock_path = str(state_path) + '.lock'
    with FileLock(lock_path, timeout=LOCK_TIMEOUT):
        state = load_state(state_path)
        yield state
        save_state(state, state_path)


def initialize_state_from_prd(
    prd_path: Path,
    state_path: Path,
) -> WorkflowState:
    """Read a prd.json file and create a WorkflowState with one StoryWorkflow per story.

    Supports two PRD formats:
    - Flat array: ``[{"id": "...", "title": "...", ...}, ...]``
    - Rich format: ``{"stories": [...], ...}``

    Stories start with status=unclaimed, empty steps (populated when claimed),
    and empty history. If PRD stories have a ``depends_on`` field, it is preserved.
    """
    raw = json.loads(prd_path.read_text(encoding='utf-8'))

    # Normalise to a list of story dicts
    stories_raw: list[dict[str, Any]]
    if isinstance(raw, list):
        stories_raw = raw
    elif isinstance(raw, dict) and 'stories' in raw:
        stories_raw = raw['stories']
    else:
        raise ValueError(
            f"Unrecognised PRD format in {prd_path}: expected a JSON array or an object with a 'stories' key."
        )

    stories: dict[str, StoryWorkflow] = {}
    for entry in stories_raw:
        story_id = str(entry.get('id', ''))
        if not story_id:
            raise ValueError(f"PRD story missing 'id' field: {entry}")

        title = str(entry.get('title', ''))
        description = str(entry.get('description', ''))
        acceptance_criteria = list(entry.get('acceptanceCriteria', entry.get('acceptance_criteria', [])))
        depends_on = list(entry.get('depends_on', []))

        stories[story_id] = StoryWorkflow(
            story_id=story_id,
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            status=StoryStatus.unclaimed,
            depends_on=depends_on,
            steps=[],
            history=[],
        )

    now = datetime.now(UTC).isoformat()
    state = WorkflowState(
        version=1,
        created_at=now,
        prd_file=str(prd_path),
        stories=stories,
    )

    validate_dependency_graph(state)
    save_state(state, state_path)
    return state


def find_assignable_story(state: WorkflowState) -> StoryWorkflow | None:
    """Find the first unclaimed story whose dependencies are all completed.

    Returns ``None`` if no story is currently assignable.
    """
    completed_ids: set[str] = {sid for sid, sw in state.stories.items() if sw.status == StoryStatus.completed}

    for story in state.stories.values():
        if story.status != StoryStatus.unclaimed:
            continue
        if all(dep in completed_ids for dep in story.depends_on):
            return story

    return None


def validate_dependency_graph(state: WorkflowState) -> None:
    """Validate the dependency graph via topological sort.

    Raises ``ValueError`` listing the cycle if circular dependencies are detected.
    """
    # Build adjacency list and in-degree map
    in_degree: dict[str, int] = defaultdict(int)
    dependents: dict[str, list[str]] = defaultdict(list)

    all_ids = set(state.stories.keys())

    for sid, story in state.stories.items():
        if sid not in in_degree:
            in_degree[sid] = 0
        for dep in story.depends_on:
            if dep not in all_ids:
                raise ValueError(f"Story '{sid}' depends on '{dep}' which does not exist.")
            dependents[dep].append(sid)
            in_degree[sid] += 1

    # Kahn's algorithm
    queue: deque[str] = deque(sid for sid, deg in in_degree.items() if deg == 0)
    visited_count = 0

    while queue:
        node = queue.popleft()
        visited_count += 1
        for dependent in dependents[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if visited_count != len(all_ids):
        # Find the cycle by collecting nodes still with non-zero in-degree
        cycle_members = [sid for sid, deg in in_degree.items() if deg > 0]

        # Trace one concrete cycle for a helpful error message
        cycle = _trace_cycle(cycle_members, state)
        cycle_str = ' -> '.join(cycle)
        raise ValueError(f'Circular dependency detected: {cycle_str}')


def _trace_cycle(cycle_members: list[str], state: WorkflowState) -> list[str]:
    """Trace a concrete cycle path from the set of nodes involved in a cycle."""
    member_set = set(cycle_members)
    if not member_set:
        return []

    start = cycle_members[0]
    visited: set[str] = set()
    path: list[str] = [start]
    current = start

    while True:
        # Follow a dependency that is also in the cycle
        next_node = None
        for dep in state.stories[current].depends_on:
            if dep in member_set:
                next_node = dep
                break

        if next_node is None:
            break

        if next_node in visited:
            # We found the cycle — trim path to start at the repeated node
            cycle_start = path.index(next_node)
            return [*path[cycle_start:], next_node]

        visited.add(next_node)
        path.append(next_node)
        current = next_node

    # Fallback: just return the members with the first repeated
    return [*cycle_members, cycle_members[0]]
