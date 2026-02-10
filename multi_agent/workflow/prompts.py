"""Per-step prompt templates and prompt composition for Dynamic Ralph."""

from __future__ import annotations

from multi_agent.workflow.models import Step, StepStatus, StepType, StoryWorkflow


# ---------------------------------------------------------------------------
# Per-step instruction templates
# ---------------------------------------------------------------------------

STEP_INSTRUCTIONS: dict[StepType, str] = {
    StepType.context_gathering: """\
## Step: Context Gathering

**You receive:** Story description, acceptance criteria, global scratch file, story scratch file.
**You produce:** Context summary listing: relevant files with paths, DB models/schemas, \
existing patterns, related tests, current behavior.

### Instructions
- Pure exploration — read code, grep for patterns, check models and schemas.
- Do NOT make decisions or plan. Just gather context.
- Write all findings to your story scratch file.
- Identify: target files, related models, existing test patterns, current behavior.

### Exit Criteria
All areas relevant to the story are identified and documented.

End your response with a SUMMARY section (3-5 lines) capturing key findings.""",
    StepType.planning: """\
## Step: Planning

**You receive:** Notes from context_gathering, story acceptance criteria, scratch files.
**You produce:** Implementation plan: what to change, in what order, which approach, which files.

### Instructions
- Focus on decision-making based on gathered context.
- If the story is more complex than a single coding round, use workflow editing to split/add steps.
- For simple stories, skip unnecessary steps (e.g., skip test_architecture for migration-only work).
- Write the plan to your story scratch file.

### Workflow Editing
You may modify remaining steps. Write a JSON file to `workflow_edits/{story_id}.json` \
with operations: add_after, split, skip, reorder, edit_description.

### Exit Criteria
Plan covers all acceptance criteria; files to modify are identified.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.architecture: """\
## Step: Architecture

**You receive:** Notes from context_gathering + planning, scratch files.
**You produce:** Architecture notes: new/modified files, schema changes, migration needs, \
import dependencies, layer boundary compliance.

### Instructions
- Design the technical structure.
- Verify it fits within `api -> core -> common` layering.
- If migration is needed, note it explicitly.
- May add/split coding steps via workflow editing.

### Workflow Editing
You may modify remaining steps if needed.

### Exit Criteria
All structural decisions documented; import dependencies verified.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.test_architecture: """\
## Step: Test Architecture

**You receive:** Notes from architecture, existing test patterns, scratch files.
**You produce:** Test plan: test files, test classes, key scenarios, fixtures needed, edge cases.

### Instructions
- Design tests independently from implementation.
- Cover all acceptance criteria.
- Identify which fixtures exist and which need creation.
- Your test plan will be used by the coding step.

### Workflow Editing
You may adjust strategy if architecture needs revision, or split testing phases.

### Exit Criteria
Test plan covers all acceptance criteria; fixture requirements identified.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.coding: """\
## Step: Coding

**You receive:** Notes from architecture + test_architecture, story scratch file.
**You produce:** Modified/created files committed to git.

### Instructions
- Implement production code and tests according to the plans from prior steps.
- Use `uv run` for all Python commands.
- Commit your changes with a descriptive message.
- If you discover unexpected complexity, use workflow editing to add steps.

### Workflow Editing
You may add additional coding rounds or other steps.

### Exit Criteria
All planned changes implemented; code compiles/imports without error.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.linting: """\
## Step: Linting

**You receive:** Current codebase state.
**You produce:** Clean lint/format pass, fixes committed.

### Instructions
- Run `uv run pre-commit run -a`.
- Fix any issues found.
- Re-run until clean.
- Commit fixes with message "style: fix lint issues".

### Exit Criteria
`uv run pre-commit run -a` passes with zero issues.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.initial_testing: """\
## Step: Initial Testing

**You receive:** Notes from test_architecture, current codebase.
**You produce:** Test results with pass/fail per test, categorized failures if any.

### Instructions
- Run tests using `./bin/run_agent_tests.sh <test_path>`.
- If tests fail, categorize root causes.
- Use workflow editing to add `coding -> linting -> initial_testing` fix cycle if needed.

### Workflow Editing
You may add coding + linting + testing cycles to fix failures.

### Exit Criteria
All relevant tests executed; failures documented with root causes.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.review: """\
## Step: Review

**You receive:** All prior step notes, acceptance criteria, test results, scratch files.
**You produce:** Review notes verifying each acceptance criterion with specific code references.

### Instructions
- For each acceptance criterion, cite the specific file and line that implements it.
- If you cannot cite a specific location, the criterion is NOT met — flag it.
- Check error handling, edge cases, layer boundaries.
- If issues found, use workflow editing to add fix steps.

### Workflow Editing
You may add steps for additional fixes or testing rounds.

### Exit Criteria
All acceptance criteria verified; no obvious issues remain.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.prune_tests: """\
## Step: Prune Tests

**You receive:** Current test suite, all prior step notes.
**You produce:** Pruned test files committed.

### Instructions
- Remove tests that duplicate coverage or test implementation details rather than behavior.
- Justify each removal.
- Do NOT remove tests that cover distinct edge cases or acceptance criteria.
- Commit removals.

### Exit Criteria
No redundant tests remain; coverage of acceptance criteria preserved.

End your response with a SUMMARY section (3-5 lines).""",
    StepType.final_review: """\
## Step: Final Review

**You receive:** All prior step notes, full story context, scratch files.
**You produce:** Final verification that everything passes, clean final commit.

### Instructions
- Run `uv run pre-commit run -a` and verify it passes.
- Run `./bin/run_agent_tests.sh <test_path>` and verify tests pass.
- Verify ALL acceptance criteria are met — cite file and line for each.
- If issues found, add fix steps before this step via workflow editing, then they will \
run before this step re-executes.
- Create a clean final commit summarizing the story's changes.

### Workflow Editing
You may add steps BEFORE this step if issues are found. This step cannot be removed.

### Exit Criteria
All acceptance criteria pass; tests pass; lint passes; commit is clean.

End your response with a SUMMARY section (3-5 lines).""",
}


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------


def compose_step_prompt(
    story: StoryWorkflow,
    step: Step,
    global_scratch: str,
    story_scratch: str,
    base_instructions: str,
) -> str:
    """Build the full prompt for a step invocation.

    Assembles:
    1. Base agent instructions (project conventions)
    2. Story context (description, acceptance criteria)
    3. Step-specific instructions
    4. Notes from all completed prior steps
    5. Scratch file contents
    6. Current step description (may be customized by workflow edits)
    """
    parts: list[str] = []

    # 1. Story context
    parts.append(f'# Story: {story.title}')
    parts.append(f'\n**Story ID:** {story.story_id}')
    parts.append(f'\n**Description:**\n{_get_story_description(story)}')

    ac = _get_story_acceptance_criteria(story)
    if ac:
        parts.append('\n**Acceptance Criteria:**')
        for criterion in ac:
            parts.append(f'- {criterion}')

    # 2. Step instructions
    step_instructions = STEP_INSTRUCTIONS.get(step.type, '')
    if step_instructions:
        parts.append(f'\n---\n\n{step_instructions}')

    # 3. Current step description (may be customized)
    if step.description:
        parts.append(f'\n**Current step task:** {step.description}')

    # 4. Notes from completed prior steps
    prior_notes = _collect_prior_notes(story, step)
    if prior_notes:
        parts.append('\n---\n\n## Context from Prior Steps\n')
        parts.append(prior_notes)

    # 5. Scratch files
    if global_scratch.strip():
        parts.append('\n---\n\n## Global Scratch (shared across stories)\n')
        parts.append(global_scratch.strip())

    if story_scratch.strip():
        parts.append(f'\n---\n\n## Story Scratch ({story.story_id})\n')
        parts.append(story_scratch.strip())

    # 6. Workflow editing instructions (if step allows it)
    from multi_agent.workflow.steps import STEP_ALLOWS_EDITING

    if STEP_ALLOWS_EDITING.get(step.type, False):
        parts.append('\n---\n\n## Workflow Editing\n')
        parts.append(
            f'To modify remaining steps, write a JSON file to '
            f'`workflow_edits/{story.story_id}.json`.\n'
            f'Supported operations: add_after, split, skip, reorder, '
            f'edit_description, restart.\n'
            f'See the step instructions above for when to use editing.'
        )

    return '\n'.join(parts)


def _get_story_description(story: StoryWorkflow) -> str:
    """Extract the story description."""
    return story.description or story.title


def _get_story_acceptance_criteria(story: StoryWorkflow) -> list[str]:
    """Extract acceptance criteria from story metadata."""
    return story.acceptance_criteria


def _collect_prior_notes(story: StoryWorkflow, current_step: Step) -> str:
    """Collect notes from all completed steps before the current step."""
    lines: list[str] = []
    for step in story.steps:
        if step.id == current_step.id:
            break
        if step.status == StepStatus.completed and step.notes:
            lines.append(f'### {step.type} ({step.id})')
            lines.append(step.notes)
            lines.append('')
    return '\n'.join(lines)
