"""Prompt composition for multi-agent PROPOSE / DEBATE / VOTE phases.

Builds per-agent prompts by concatenating sections in codex-specified order:
IDENTITY → CODEX → TASK FRAMING → PRIOR PROPOSALS (round 2+) → PRIOR DEBATE (round 3).

Vote prompts omit task framing per spec (identity + codex + proposals + debate).
"""

from __future__ import annotations

import string
from pathlib import Path

from multi_agent.constants import RALPH_INTERNAL_DOCS
from multi_agent.parsing import parse_proposal


# ---------------------------------------------------------------------------
# Path defaults (derived from RALPH_INTERNAL_DOCS, with package data fallback)
# ---------------------------------------------------------------------------

_PACKAGE_DATA_DOCS = Path(__file__).resolve().parent / '_data' / 'docs'


def _resolve_docs_dir() -> Path:
    """Return the docs directory, preferring /opt/ralph/docs then package data."""
    internal = Path(RALPH_INTERNAL_DOCS) / 'docs'
    if internal.is_dir():
        return internal
    if _PACKAGE_DATA_DOCS.is_dir():
        return _PACKAGE_DATA_DOCS
    return internal  # fallback to original (will fail at read time with a clear path)


RALPH_DOCS = _resolve_docs_dir()
IDENTITIES_DIR = RALPH_DOCS / 'identities'
CODEX_PATH = RALPH_DOCS / 'multi_agent_codex.md'

_SEPARATOR = '\n\n---\n\n'


# ---------------------------------------------------------------------------
# File loaders
# ---------------------------------------------------------------------------


def load_identity(identity_name: str, *, base_path: Path | None = None) -> str:
    """Read an identity file from the identities directory.

    Parameters
    ----------
    identity_name:
        Filename (e.g. ``"i_consul.md"``).
    base_path:
        Override for the identities directory.  Defaults to
        ``/opt/ralph/docs/identities/``.
    """
    directory = base_path or IDENTITIES_DIR
    return (directory / identity_name).read_text()


def load_codex(codex_path: Path | None = None) -> str:
    """Read the multi-agent codex document.

    Parameters
    ----------
    codex_path:
        Override path.  Defaults to ``/opt/ralph/docs/multi_agent_codex.md``.
    """
    path = codex_path or CODEX_PATH
    return path.read_text()


# ---------------------------------------------------------------------------
# Concatenation helpers
# ---------------------------------------------------------------------------


def concatenate_proposals(proposals: dict[str, str]) -> str:
    """Label proposals alphabetically and concatenate for debate/vote input.

    *proposals* maps identity name → proposal text.  Keys are iterated in
    insertion order; the first entry becomes Proposal A, the second Proposal B,
    and so on.
    """
    parts: list[str] = []
    for idx, (identity, text) in enumerate(proposals.items()):
        label = string.ascii_uppercase[idx]
        parts.append(f'## Proposal {label}: {identity}\n\n{text}')
    return '\n\n'.join(parts)


def concatenate_debate(debate_entries: dict[str, str]) -> str:
    """Concatenate debate entries for vote input.

    *debate_entries* maps identity name → debate text.
    """
    parts: list[str] = []
    for identity, text in debate_entries.items():
        parts.append(f'## Debate: {identity}\n\n{text}')
    return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


def check_quality_gate(proposal_text: str) -> bool:
    """Check whether a proposal passes the codex quality gate.

    A proposal passes if:
    - All required sections parse successfully (via ``parse_proposal``)
    - The *Code sketch* section contains at least one code fence (```)
    """
    sections, _diag = parse_proposal(proposal_text, agent_label='gate')
    if sections is None:
        return False
    code_sketch = sections.get('Code sketch', '')
    return '```' in code_sketch


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_PROPOSE_TASK = (
    '## Your Task\n\n'
    'Write your proposal following the codex format. '
    'Include: Summary, Code sketch, Files changed, Migration plan, '
    "What I'd argue, What worries me."
)

_DEBATE_TASK = (
    '## Your Task\n\n'
    'Write your debate entry following the codex format. '
    'Include: My case, Challenges to other proposals, '
    "What I'd adopt from others, My biggest doubt."
)

_VOTE_TASK = (
    '## Your Task\n\n'
    'Cast your vote following the codex format. '
    'Include: Winner, Decisive argument, Concerns about the winner. '
    'Optional: Unrefuted arguments, Merge suggestion.'
)


def build_propose_prompt(
    identity_text: str,
    codex_text: str,
    frame_text: str,
    prior_context: str | None = None,
    task_instructions: str | None = None,
) -> str:
    """Compose the PROPOSE phase prompt.

    Order: IDENTITY → CODEX → [PRIOR CONTEXT] → task instructions → FRAME.
    Frame text is placed last so user-specific instructions take precedence.
    *prior_context* is included only when provided (rounds 2+).
    *task_instructions* overrides the default ``_PROPOSE_TASK`` when provided.
    """
    parts = [identity_text, codex_text]
    if prior_context:
        parts.append(f'## Prior Round Context\n\n{prior_context}')
    parts.append(task_instructions or _PROPOSE_TASK)
    parts.append(frame_text)
    return _SEPARATOR.join(parts)


def build_debate_prompt(
    identity_text: str,
    codex_text: str,
    frame_text: str,
    all_proposals_text: str,
    prior_context: str | None = None,
    task_instructions: str | None = None,
) -> str:
    """Compose the DEBATE phase prompt.

    Order: IDENTITY → CODEX → ALL PROPOSALS → [PRIOR CONTEXT] → task instructions → FRAME.
    Frame text is placed last so user-specific instructions take precedence.
    *task_instructions* overrides the default ``_DEBATE_TASK`` when provided.
    """
    parts = [identity_text, codex_text, all_proposals_text]
    if prior_context:
        parts.append(f'## Prior Round Context\n\n{prior_context}')
    parts.append(task_instructions or _DEBATE_TASK)
    parts.append(frame_text)
    return _SEPARATOR.join(parts)


def build_vote_prompt(
    identity_text: str,
    codex_text: str,
    all_proposals_text: str,
    all_debate_text: str,
    task_instructions: str | None = None,
    frame_text: str | None = None,
) -> str:
    """Compose the VOTE phase prompt.

    Order: IDENTITY → CODEX → ALL PROPOSALS → ALL DEBATE → task instructions → FRAME.
    Frame text is placed last so user-specific instructions take precedence.
    *task_instructions* overrides the default ``_VOTE_TASK`` when provided.
    """
    parts = [identity_text, codex_text, all_proposals_text, all_debate_text]
    parts.append(task_instructions or _VOTE_TASK)
    if frame_text:
        parts.append(frame_text)
    return _SEPARATOR.join(parts)
