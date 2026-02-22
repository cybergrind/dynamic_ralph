"""Tests for multi_agent.parsing module."""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent.parsing import (
    ParseDiagnostic,
    _parse_concerns,
    _parse_list,
    parse_proposal,
    parse_sections,
    parse_vote,
    summarize_phase_health,
    write_phase_diagnostics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_diagnostic(
    agent_label: str = 'A',
    phase: str = 'vote',
    sections_found: list[str] | None = None,
    sections_missing: list[str] | None = None,
    headings_seen: list[str] | None = None,
    raw_text: str = '',
    parse_succeeded: bool = True,
) -> ParseDiagnostic:
    return ParseDiagnostic(
        agent_label=agent_label,
        phase=phase,
        sections_found=sections_found or [],
        sections_missing=sections_missing or [],
        headings_seen=headings_seen or [],
        raw_text=raw_text,
        parse_succeeded=parse_succeeded,
    )


def _make_vote_text(
    winner: str = 'A',
    decisive: str = 'Strong argument about performance',
    concerns: str = 'A: Memory usage could be high',
    unrefuted: str = '- Point one\n- Point two',
    merge: str | None = None,
) -> str:
    text = f"""## Winner
{winner}

## Decisive argument
{decisive}

## Concerns about the winner
{concerns}
"""
    if unrefuted:
        text += f'\n## Unrefuted arguments\n{unrefuted}\n'
    if merge:
        text += f'\n## Merge suggestion\n{merge}\n'
    return text


def _make_proposal_text(
    summary: str = 'A proposal to refactor the parser.',
    code_sketch: str = '```python\ndef parse(): ...\n```',
    files_changed: str = '- multi_agent/parsing.py',
    migration: str = 'No migration needed.',
    argue: str = 'This is the simplest approach.',
    worries: str = 'Might not handle edge cases.',
) -> str:
    return f"""## Summary
{summary}

## Code sketch
{code_sketch}

## Files changed
{files_changed}

## Migration plan
{migration}

## What I'd argue
{argue}

## What worries me
{worries}
"""


# ---------------------------------------------------------------------------
# parse_sections — basic heading matching
# ---------------------------------------------------------------------------


class TestParseSections:
    """Tests for parse_sections()."""

    def test_basic_required_sections(self) -> None:
        text = '## Summary\nHello world\n\n## Details\nSome details here'
        sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert sections == {'Summary': 'Hello world', 'Details': 'Some details here'}
        assert diag.parse_succeeded is True
        assert diag.sections_found == ['Summary', 'Details']
        assert diag.sections_missing == []

    def test_missing_required_section(self) -> None:
        text = '## Summary\nHello world'
        _sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is False
        assert 'Details' in diag.sections_missing
        assert 'Summary' in diag.sections_found

    def test_optional_sections(self) -> None:
        text = '## Summary\nHello\n\n## Notes\nSome notes'
        sections, diag = parse_sections(text, required=['Summary'], optional=['Notes', 'Extra'])
        assert diag.parse_succeeded is True
        assert 'Notes' in sections
        assert 'Extra' not in sections
        assert diag.sections_found == ['Summary', 'Notes']

    def test_case_insensitive_matching(self) -> None:
        text = '## summary\nlower case heading'
        sections, diag = parse_sections(text, required=['Summary'])
        assert diag.parse_succeeded is True
        assert 'Summary' in sections
        assert sections['Summary'] == 'lower case heading'

    def test_headings_seen_includes_all(self) -> None:
        text = '## Summary\nHello\n\n## Unknown heading\nStuff\n\n## Details\nMore'
        _sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert 'Summary' in diag.headings_seen
        assert 'Unknown heading' in diag.headings_seen
        assert 'Details' in diag.headings_seen
        assert len(diag.headings_seen) == 3

    def test_multiple_heading_levels(self) -> None:
        text = '# Summary\nLevel 1\n\n### Details\nLevel 3'
        sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is True
        assert sections['Summary'] == 'Level 1'
        assert sections['Details'] == 'Level 3'

    def test_empty_text(self) -> None:
        sections, diag = parse_sections('', required=['Summary'])
        assert diag.parse_succeeded is False
        assert sections == {}
        assert diag.sections_missing == ['Summary']

    def test_raw_text_preserved(self) -> None:
        text = '## Summary\nHello'
        _, diag = parse_sections(text, required=['Summary'])
        assert diag.raw_text == text

    def test_agent_label_and_phase_default_empty(self) -> None:
        _, diag = parse_sections('## Summary\nHi', required=['Summary'])
        assert diag.agent_label == ''
        assert diag.phase == ''

    def test_content_between_sections_preserved(self) -> None:
        text = '## Summary\nLine 1\nLine 2\n\nLine 3\n\n## Details\nD'
        sections, _ = parse_sections(text, required=['Summary', 'Details'])
        assert sections['Summary'] == 'Line 1\nLine 2\n\nLine 3'


# ---------------------------------------------------------------------------
# parse_sections — code fence handling
# ---------------------------------------------------------------------------


class TestParseSectionsCodeFences:
    """Tests for code-fence-aware heading matching."""

    def test_backtick_fence_skips_headings(self) -> None:
        text = (
            '## Summary\nBefore code\n'
            '```\n## Summary\nThis is inside a code block\n```\n'
            'After code\n'
            '## Details\nReal details'
        )
        sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is True
        # The heading inside the fence should NOT split the Summary section
        assert 'inside a code block' in sections['Summary']
        assert sections['Details'] == 'Real details'

    def test_tilde_fence_skips_headings(self) -> None:
        text = '## Summary\nBefore code\n~~~\n## Summary\nInside tilde fence\n~~~\nAfter code\n## Details\nReal details'
        sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is True
        assert 'Inside tilde fence' in sections['Summary']

    def test_headings_inside_fence_not_in_headings_seen(self) -> None:
        text = '## Summary\nHello\n```\n## Fake\nfake\n```\n## Details\nWorld'
        _, diag = parse_sections(text, required=['Summary', 'Details'])
        assert 'Fake' not in diag.headings_seen
        assert 'Summary' in diag.headings_seen
        assert 'Details' in diag.headings_seen

    def test_nested_code_fence_in_proposal(self) -> None:
        """Realistic test: code sketch with markdown headings inside."""
        text = (
            '## Summary\nRefactor the parser\n\n'
            '## Code sketch\n'
            '```python\n'
            '## Summary\n'
            '# This is a comment, not a heading\n'
            'def parse():\n'
            '    pass\n'
            '```\n\n'
            '## Files changed\n- parsing.py\n\n'
            '## Migration plan\nNone\n\n'
            "## What I'd argue\nSimplicity\n\n"
            '## What worries me\nEdge cases'
        )
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
        assert diag.parse_succeeded is True
        assert 'def parse():' in sections['Code sketch']
        # The ## Summary inside the fence should not appear as a separate section
        assert sections['Summary'] == 'Refactor the parser'

    def test_fence_opened_but_not_closed(self) -> None:
        """Unclosed fence: all subsequent headings should be ignored."""
        text = '## Summary\nHello\n```\n## Details\nInside unclosed fence'
        sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is False
        assert 'Details' in diag.sections_missing
        assert 'Inside unclosed fence' in sections['Summary']

    def test_multiple_code_blocks(self) -> None:
        text = (
            '## Summary\nFirst\n'
            '```\n## Fake1\nfake\n```\n'
            'Middle content\n'
            '~~~\n## Fake2\nfake\n~~~\n'
            'End content\n'
            '## Details\nReal'
        )
        _sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is True
        assert 'Fake1' not in diag.headings_seen
        assert 'Fake2' not in diag.headings_seen

    def test_fence_with_language_tag(self) -> None:
        text = '## Summary\nHello\n```python\n## Fake\ncode\n```\n## Details\nWorld'
        _sections, diag = parse_sections(text, required=['Summary', 'Details'])
        assert diag.parse_succeeded is True
        assert 'Fake' not in diag.headings_seen


# ---------------------------------------------------------------------------
# parse_vote
# ---------------------------------------------------------------------------


class TestParseVote:
    """Tests for parse_vote()."""

    def test_basic_vote(self) -> None:
        text = _make_vote_text()
        result, diag = parse_vote(text, 'B')
        assert result is not None
        assert result.voter_label == 'B'
        assert result.winner == 'A'
        assert diag.parse_succeeded is True
        assert diag.agent_label == 'B'
        assert diag.phase == 'vote'

    def test_normalize_proposal_prefix(self) -> None:
        """'Proposal B' should normalize to 'B'."""
        text = _make_vote_text(winner='Proposal B')
        result, _ = parse_vote(text, 'A')
        assert result is not None
        assert result.winner == 'B'

    def test_normalize_lowercase_proposal_prefix(self) -> None:
        """'proposal b' should normalize to 'B'."""
        text = _make_vote_text(winner='proposal b')
        result, _ = parse_vote(text, 'A')
        assert result is not None
        assert result.winner == 'B'

    def test_normalize_bare_label(self) -> None:
        """'B' stays 'B'."""
        text = _make_vote_text(winner='B')
        result, _ = parse_vote(text, 'A')
        assert result is not None
        assert result.winner == 'B'

    def test_normalize_lowercase_bare_label(self) -> None:
        """'b' normalizes to 'B'."""
        text = _make_vote_text(winner='b')
        result, _ = parse_vote(text, 'A')
        assert result is not None
        assert result.winner == 'B'

    def test_valid_proposals_accepted(self) -> None:
        text = _make_vote_text(winner='A')
        result, diag = parse_vote(text, 'B', valid_proposals=['A', 'B'])
        assert result is not None
        assert result.winner == 'A'
        assert diag.parse_succeeded is True

    def test_valid_proposals_rejected(self) -> None:
        text = _make_vote_text(winner='C')
        result, diag = parse_vote(text, 'B', valid_proposals=['A', 'B'])
        assert result is None
        assert diag.parse_succeeded is False
        assert any('Winner' in m for m in diag.sections_missing)

    def test_valid_proposals_with_prefix_rejected(self) -> None:
        """'Proposal C' should be rejected when C is not in valid_proposals."""
        text = _make_vote_text(winner='Proposal C')
        result, diag = parse_vote(text, 'A', valid_proposals=['A', 'B'])
        assert result is None
        assert diag.parse_succeeded is False

    def test_missing_required_section(self) -> None:
        text = '## Winner\nA\n\n## Decisive argument\nGood point'
        # Missing "Concerns about the winner"
        result, diag = parse_vote(text, 'A')
        assert result is None
        assert diag.parse_succeeded is False
        assert 'Concerns about the winner' in diag.sections_missing

    def test_concerns_parsed(self) -> None:
        text = _make_vote_text(concerns='A: Too complex\nB: Memory issues')
        result, _ = parse_vote(text, 'C')
        assert result is not None
        assert result.concerns == {'A': 'Too complex', 'B': 'Memory issues'}

    def test_unrefuted_arguments_parsed(self) -> None:
        text = _make_vote_text(unrefuted='- First point\n- Second point')
        result, _ = parse_vote(text, 'C')
        assert result is not None
        assert result.unrefuted_arguments == ['First point', 'Second point']

    def test_merge_suggestion_parsed(self) -> None:
        text = _make_vote_text(merge='Combine approaches A and B')
        result, _ = parse_vote(text, 'C')
        assert result is not None
        assert result.merge_suggestion == 'Combine approaches A and B'

    def test_no_merge_suggestion(self) -> None:
        text = _make_vote_text(merge=None)
        result, _ = parse_vote(text, 'C')
        assert result is not None
        assert result.merge_suggestion is None

    def test_no_unrefuted_arguments(self) -> None:
        text = _make_vote_text(unrefuted='')
        result, _ = parse_vote(text, 'C')
        assert result is not None
        assert result.unrefuted_arguments == []

    def test_valid_proposals_none_skips_validation(self) -> None:
        text = _make_vote_text(winner='Z')
        result, diag = parse_vote(text, 'A', valid_proposals=None)
        assert result is not None
        assert result.winner == 'Z'
        assert diag.parse_succeeded is True


# ---------------------------------------------------------------------------
# parse_proposal
# ---------------------------------------------------------------------------


class TestParseProposal:
    """Tests for parse_proposal()."""

    def test_basic_proposal(self) -> None:
        text = _make_proposal_text()
        result, diag = parse_proposal(text, 'A')
        assert result is not None
        assert diag.parse_succeeded is True
        assert diag.agent_label == 'A'
        assert diag.phase == 'propose'
        assert 'Summary' in result
        assert 'Code sketch' in result
        assert 'Files changed' in result
        assert 'Migration plan' in result
        assert "What I'd argue" in result
        assert 'What worries me' in result

    def test_missing_section(self) -> None:
        text = '## Summary\nHello\n\n## Code sketch\nCode'
        result, diag = parse_proposal(text, 'A')
        assert result is None
        assert diag.parse_succeeded is False
        assert len(diag.sections_missing) > 0

    def test_all_six_required_sections(self) -> None:
        text = _make_proposal_text()
        result, _diag = parse_proposal(text, 'B')
        assert result is not None
        assert len(result) == 6

    def test_proposal_with_code_fence_in_sketch(self) -> None:
        """Code sketch containing markdown headings should not corrupt parsing."""
        text = _make_proposal_text(
            code_sketch='```python\n## Summary\ndef main():\n    pass\n```',
        )
        result, diag = parse_proposal(text, 'A')
        assert result is not None
        assert diag.parse_succeeded is True
        assert 'def main():' in result['Code sketch']


# ---------------------------------------------------------------------------
# _parse_concerns
# ---------------------------------------------------------------------------


class TestParseConcerns:
    """Tests for _parse_concerns() helper."""

    def test_basic_concerns(self) -> None:
        text = 'A: Too complex\nB: Memory issues'
        assert _parse_concerns(text) == {'A': 'Too complex', 'B': 'Memory issues'}

    def test_proposal_prefix(self) -> None:
        text = 'Proposal A: Too complex\nProposal B: Memory issues'
        assert _parse_concerns(text) == {'A': 'Too complex', 'B': 'Memory issues'}

    def test_bullet_markers(self) -> None:
        text = '- A: Too complex\n- B: Memory issues'
        assert _parse_concerns(text) == {'A': 'Too complex', 'B': 'Memory issues'}

    def test_empty_text(self) -> None:
        assert _parse_concerns('') == {}

    def test_blank_lines_skipped(self) -> None:
        text = 'A: First\n\nB: Second'
        assert _parse_concerns(text) == {'A': 'First', 'B': 'Second'}

    def test_case_insensitive_proposal_prefix(self) -> None:
        text = 'proposal A: Something'
        assert _parse_concerns(text) == {'A': 'Something'}


# ---------------------------------------------------------------------------
# _parse_list
# ---------------------------------------------------------------------------


class TestParseList:
    """Tests for _parse_list() helper."""

    def test_bullet_list(self) -> None:
        text = '- First\n- Second\n- Third'
        assert _parse_list(text) == ['First', 'Second', 'Third']

    def test_asterisk_bullets(self) -> None:
        text = '* First\n* Second'
        assert _parse_list(text) == ['First', 'Second']

    def test_numbered_list(self) -> None:
        text = '1. First\n2. Second\n3. Third'
        assert _parse_list(text) == ['First', 'Second', 'Third']

    def test_empty_text(self) -> None:
        assert _parse_list('') == []

    def test_plain_lines(self) -> None:
        text = 'First\nSecond'
        assert _parse_list(text) == ['First', 'Second']

    def test_mixed_markers(self) -> None:
        text = '- First\n* Second\n3. Third'
        assert _parse_list(text) == ['First', 'Second', 'Third']

    def test_blank_lines_skipped(self) -> None:
        text = '- First\n\n- Second'
        assert _parse_list(text) == ['First', 'Second']


# ---------------------------------------------------------------------------
# Diagnostic utilities
# ---------------------------------------------------------------------------


class TestWritePhaseDiagnostics:
    """Tests for write_phase_diagnostics()."""

    def test_writes_jsonl(self, tmp_path: Path) -> None:
        diags = [
            _make_diagnostic(agent_label='A', parse_succeeded=True),
            _make_diagnostic(agent_label='B', parse_succeeded=False, sections_missing=['Winner']),
        ]
        write_phase_diagnostics(diags, 'vote', tmp_path)
        path = tmp_path / 'diagnostics.jsonl'
        assert path.exists()
        lines = path.read_text().strip().split('\n')
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first['agent_label'] == 'A'
        assert first['parse_succeeded'] is True
        second = json.loads(lines[1])
        assert second['agent_label'] == 'B'
        assert second['parse_succeeded'] is False

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        diags1 = [_make_diagnostic(agent_label='A')]
        diags2 = [_make_diagnostic(agent_label='B')]
        write_phase_diagnostics(diags1, 'vote', tmp_path)
        write_phase_diagnostics(diags2, 'propose', tmp_path)
        path = tmp_path / 'diagnostics.jsonl'
        lines = path.read_text().strip().split('\n')
        assert len(lines) == 2


class TestSummarizePhaseHealth:
    """Tests for summarize_phase_health()."""

    def test_all_succeeded(self) -> None:
        diags = [
            _make_diagnostic(agent_label='A', parse_succeeded=True),
            _make_diagnostic(agent_label='B', parse_succeeded=True),
        ]
        result = summarize_phase_health(diags)
        assert result == '2/2 parsed'

    def test_some_failed(self) -> None:
        diags = [
            _make_diagnostic(agent_label='A', parse_succeeded=True),
            _make_diagnostic(
                agent_label='B',
                parse_succeeded=False,
                sections_missing=['Decisive argument'],
            ),
        ]
        result = summarize_phase_health(diags)
        assert '1/2 parsed' in result
        assert 'B: missing Decisive argument' in result

    def test_multiple_failures(self) -> None:
        diags = [
            _make_diagnostic(
                agent_label='A',
                parse_succeeded=False,
                sections_missing=['Winner'],
            ),
            _make_diagnostic(
                agent_label='B',
                parse_succeeded=False,
                sections_missing=['Decisive argument'],
            ),
            _make_diagnostic(agent_label='C', parse_succeeded=True),
        ]
        result = summarize_phase_health(diags)
        assert '1/3 parsed' in result
        assert 'A: missing Winner' in result
        assert 'B: missing Decisive argument' in result

    def test_empty_diagnostics(self) -> None:
        result = summarize_phase_health([])
        assert result == '0/0 parsed'
