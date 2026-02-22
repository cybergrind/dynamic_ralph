"""Output parsing for multi-agent workflow.

Converts agent markdown output into structured data. The parser is on the
critical path: if it fails, the system produces wrong decisions regardless of
how robust the process lifecycle is.

Key design choices:
- Code-fence-aware heading matching (tracks ``` and ~~~ state)
- Strict case-insensitive heading matching (no fuzzy matching in v1)
- Every parse attempt returns a ParseDiagnostic alongside the result

Known gaps acceptable for v1:
- Indented code blocks (4-space) are not tracked
- Blockquote-nested fences (> ```) are not tracked
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ParseDiagnostic:
    """Records what was found and what was missing when parsing agent output."""

    agent_label: str
    phase: str  # "propose", "debate", "vote"
    sections_found: list[str]  # e.g. ["Summary", "Code sketch"]
    sections_missing: list[str]  # e.g. ["Migration plan"]
    headings_seen: list[str] = field(  # ALL headings found in agent output
        default_factory=list,  # (matched and unmatched) for debugging
    )
    raw_text: str = ''  # full agent output for fallback
    parse_succeeded: bool = False


@dataclass
class VoteResult:
    """Structured output from a single agent's vote."""

    voter_label: str  # e.g. "A", "B"
    winner: str  # proposal label voted for
    decisive_argument: str  # required: the debate argument that convinced
    concerns: dict[str, str] = field(  # proposal_label -> concern text
        default_factory=dict,
    )
    unrefuted_arguments: list[str] = field(  # optional
        default_factory=list,
    )
    merge_suggestion: str | None = None  # optional


# ---------------------------------------------------------------------------
# Core parser
# ---------------------------------------------------------------------------

_PROPOSAL_PREFIX_RE = re.compile(r'^proposal\s+', re.IGNORECASE)


def parse_sections(
    text: str,
    required: list[str],
    optional: list[str] | None = None,
) -> tuple[dict[str, str], ParseDiagnostic]:
    """Parse markdown sections from agent output.

    Uses strict case-insensitive heading matching for v1:
    - Track fenced code block state (``` and ~~~) to skip headings inside code
    - Strip leading '#' and whitespace from each line
    - Match against required/optional section names (exact, case-insensitive)
    - Record ALL headings found (matched and unmatched) in the diagnostic

    Known gaps acceptable for v1:
    - Indented code blocks (4-space) are not tracked
    - Blockquote-nested fences (> ```) are not tracked

    Returns (sections_dict, diagnostic).
    """
    optional = optional or []
    expected = {name.lower(): name for name in required + optional}
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_lines: list[str] = []
    headings_seen: list[str] = []
    in_code_fence = False

    for line in text.splitlines():
        stripped = line.strip()

        # Track fenced code block state (``` and ~~~).
        # A line starting with ``` or ~~~ toggles fence state.
        if stripped.startswith(('```', '~~~')):
            in_code_fence = not in_code_fence
            if current_section is not None:
                current_lines.append(line)
            continue

        # Skip heading matching inside fenced code blocks.
        if in_code_fence:
            if current_section is not None:
                current_lines.append(line)
            continue

        heading_text = line.lstrip('#').strip()
        key = heading_text.lower()

        # Record every heading we encounter (for headings_seen diagnostic)
        if line.lstrip().startswith('#') and heading_text:
            headings_seen.append(heading_text)

        if key in expected:
            if current_section is not None:
                sections[current_section] = '\n'.join(current_lines).strip()
            current_section = expected[key]
            current_lines = []
        elif current_section is not None:
            current_lines.append(line)

    if current_section is not None:
        sections[current_section] = '\n'.join(current_lines).strip()

    found = [name for name in required + optional if name in sections]
    missing = [name for name in required if name not in sections]

    diag = ParseDiagnostic(
        agent_label='',  # filled in by caller
        phase='',  # filled in by caller
        sections_found=found,
        sections_missing=missing,
        headings_seen=headings_seen,
        raw_text=text,
        parse_succeeded=len(missing) == 0,
    )
    return sections, diag


# ---------------------------------------------------------------------------
# Helper parsers
# ---------------------------------------------------------------------------


def _parse_concerns(text: str) -> dict[str, str]:
    """Parse concern lines of the form 'Proposal X: concern text' or 'X: concern text'.

    Returns a dict mapping normalized proposal labels to concern text.
    """
    concerns: dict[str, str] = {}
    if not text.strip():
        return concerns

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading bullet markers (- or *)
        if line.startswith(('- ', '* ')):
            line = line[2:].strip()
        # Match "Proposal X: text" or "X: text"
        match = re.match(r'^(?:proposal\s+)?([A-Za-z0-9]+)\s*:\s*(.+)', line, re.IGNORECASE)
        if match:
            label = match.group(1).upper()
            concern = match.group(2).strip()
            concerns[label] = concern
    return concerns


def _parse_list(text: str) -> list[str]:
    """Parse a bullet or numbered list from text.

    Recognizes lines starting with '- ', '* ', or 'N. ' (numbered).
    Returns a list of stripped item strings, or an empty list for blank input.
    """
    items: list[str] = []
    if not text.strip():
        return items

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip bullet markers
        if line.startswith(('- ', '* ')):
            items.append(line[2:].strip())
        # Strip numbered list markers (e.g. "1. ", "2. ")
        elif re.match(r'^\d+\.\s+', line):
            items.append(re.sub(r'^\d+\.\s+', '', line).strip())
        else:
            # Plain line — include as-is
            items.append(line)
    return items


# ---------------------------------------------------------------------------
# Phase-specific parsers
# ---------------------------------------------------------------------------


def parse_vote(
    text: str,
    agent_label: str,
    valid_proposals: list[str] | None = None,
) -> tuple[VoteResult | None, ParseDiagnostic]:
    """Parse a vote from agent output. Returns (result, diagnostic).

    If *valid_proposals* is provided, the winner field is normalized (strip
    "Proposal " prefix, uppercase) and validated against the known labels.
    Votes for unknown proposals return None to prevent phantom tally entries.
    """
    sections, diag = parse_sections(
        text,
        required=['Winner', 'Decisive argument', 'Concerns about the winner'],
        optional=['Unrefuted arguments', 'Merge suggestion'],
    )
    diag.agent_label = agent_label
    diag.phase = 'vote'
    if not diag.parse_succeeded:
        return None, diag

    # Normalize winner: "Proposal B" -> "B", "proposal b" -> "B", "B" -> "B"
    raw_winner = sections['Winner'].strip()
    normalized = _PROPOSAL_PREFIX_RE.sub('', raw_winner).strip().upper()

    if valid_proposals is not None and normalized not in valid_proposals:
        diag.parse_succeeded = False
        diag.sections_missing.append(f"Winner (got '{raw_winner}', expected one of {valid_proposals})")
        return None, diag

    return VoteResult(
        voter_label=agent_label,
        winner=normalized,
        decisive_argument=sections['Decisive argument'],
        concerns=_parse_concerns(sections['Concerns about the winner']),
        unrefuted_arguments=_parse_list(sections.get('Unrefuted arguments', '')),
        merge_suggestion=sections.get('Merge suggestion'),
    ), diag


def parse_proposal(text: str, agent_label: str) -> tuple[dict[str, str] | None, ParseDiagnostic]:
    """Parse a proposal from agent output. Returns (sections_dict, diagnostic)."""
    sections, diag = parse_sections(
        text,
        required=[
            'Summary',
            'Code sketch',
            'Files changed',
            'Migration plan',
            "What I'd argue",
            'What worries me',
        ],
    )
    diag.agent_label = agent_label
    diag.phase = 'propose'
    if not diag.parse_succeeded:
        return None, diag
    return sections, diag


# ---------------------------------------------------------------------------
# Diagnostic utilities
# ---------------------------------------------------------------------------


def write_phase_diagnostics(
    diagnostics: list[ParseDiagnostic],
    phase: str,
    round_dir: Path,
) -> None:
    """Write parse diagnostics to round_dir/diagnostics.jsonl."""
    path = round_dir / 'diagnostics.jsonl'
    with open(path, 'a') as f:
        for d in diagnostics:
            f.write(json.dumps(asdict(d)) + '\n')


def summarize_phase_health(diagnostics: list[ParseDiagnostic]) -> str:
    """One-line summary: 'vote: 4/5 parsed (agent-C: missing Decisive argument)'."""
    succeeded = sum(1 for d in diagnostics if d.parse_succeeded)
    total = len(diagnostics)
    failures = [
        f'{d.agent_label}: missing {", ".join(d.sections_missing)}' for d in diagnostics if not d.parse_succeeded
    ]
    summary = f'{succeeded}/{total} parsed'
    if failures:
        summary += f' ({"; ".join(failures)})'
    return summary
