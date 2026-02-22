"""Tests for multi_agent.codex_prompts module."""

from __future__ import annotations

from pathlib import Path

import pytest

from multi_agent.codex_prompts import (
    build_debate_prompt,
    build_propose_prompt,
    build_vote_prompt,
    check_quality_gate,
    concatenate_debate,
    concatenate_proposals,
    load_codex,
    load_identity,
)


_SEPARATOR = '\n\n---\n\n'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proposal_text(*, with_code_fence: bool = True) -> str:
    """Build a valid proposal for quality-gate testing."""
    code = '```python\ndef hello(): ...\n```' if with_code_fence else 'def hello(): ...'
    return (
        '## Summary\nA brief summary.\n\n'
        f'## Code sketch\n{code}\n\n'
        '## Files changed\n- src/foo.py\n\n'
        '## Migration plan\nNothing breaks.\n\n'
        "## What I'd argue\nThis is the best approach.\n\n"
        '## What worries me\nEdge cases.\n'
    )


# ---------------------------------------------------------------------------
# TestLoadIdentity
# ---------------------------------------------------------------------------


class TestLoadIdentity:
    def test_reads_identity_file(self, tmp_path: Path) -> None:
        identity_dir = tmp_path / 'identities'
        identity_dir.mkdir()
        (identity_dir / 'i_consul.md').write_text('I am consul.')

        result = load_identity('i_consul.md', base_path=identity_dir)
        assert result == 'I am consul.'

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_identity('missing.md', base_path=tmp_path)


# ---------------------------------------------------------------------------
# TestLoadCodex
# ---------------------------------------------------------------------------


class TestLoadCodex:
    def test_reads_codex_file(self, tmp_path: Path) -> None:
        codex = tmp_path / 'codex.md'
        codex.write_text('# Codex\nRules.')

        result = load_codex(codex_path=codex)
        assert result == '# Codex\nRules.'

    def test_missing_codex_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_codex(codex_path=tmp_path / 'missing.md')


# ---------------------------------------------------------------------------
# TestConcatenateProposals
# ---------------------------------------------------------------------------


class TestConcatenateProposals:
    def test_labels_alphabetically(self) -> None:
        proposals = {'consul': 'Plan A stuff', 'pydantic': 'Plan B stuff'}
        result = concatenate_proposals(proposals)

        assert '## Proposal A: consul' in result
        assert '## Proposal B: pydantic' in result

    def test_preserves_order(self) -> None:
        proposals = {'alpha': 'text-a', 'beta': 'text-b', 'gamma': 'text-c'}
        result = concatenate_proposals(proposals)

        pos_a = result.index('Proposal A')
        pos_b = result.index('Proposal B')
        pos_c = result.index('Proposal C')
        assert pos_a < pos_b < pos_c

    def test_single_proposal(self) -> None:
        result = concatenate_proposals({'only': 'content'})
        assert '## Proposal A: only' in result
        assert 'Proposal B' not in result

    def test_empty_proposals(self) -> None:
        result = concatenate_proposals({})
        assert result == ''


# ---------------------------------------------------------------------------
# TestConcatenateDebate
# ---------------------------------------------------------------------------


class TestConcatenateDebate:
    def test_labels_with_identity(self) -> None:
        entries = {'consul': 'My case...', 'pydantic': 'My case...'}
        result = concatenate_debate(entries)

        assert '## Debate: consul' in result
        assert '## Debate: pydantic' in result

    def test_empty_entries(self) -> None:
        result = concatenate_debate({})
        assert result == ''


# ---------------------------------------------------------------------------
# TestCheckQualityGate
# ---------------------------------------------------------------------------


class TestCheckQualityGate:
    def test_passes_with_code_fence(self) -> None:
        assert check_quality_gate(_make_proposal_text(with_code_fence=True)) is True

    def test_fails_without_code_fence(self) -> None:
        assert check_quality_gate(_make_proposal_text(with_code_fence=False)) is False

    def test_fails_with_missing_sections(self) -> None:
        assert check_quality_gate('# Just a heading\nSome text.') is False


# ---------------------------------------------------------------------------
# TestBuildProposePrompt
# ---------------------------------------------------------------------------


class TestBuildProposePrompt:
    def test_ordering_identity_codex_frame(self) -> None:
        result = build_propose_prompt('IDENTITY', 'CODEX', 'FRAME')
        parts = result.split(_SEPARATOR)

        assert parts[0] == 'IDENTITY'
        assert parts[1] == 'CODEX'
        assert parts[2] == 'FRAME'

    def test_no_prior_context_by_default(self) -> None:
        result = build_propose_prompt('ID', 'CO', 'FR')
        assert 'Prior Round Context' not in result

    def test_includes_prior_context_when_provided(self) -> None:
        result = build_propose_prompt('ID', 'CO', 'FR', prior_context='prev round')
        assert '## Prior Round Context' in result
        assert 'prev round' in result

    def test_prior_context_empty_string_excluded(self) -> None:
        result = build_propose_prompt('ID', 'CO', 'FR', prior_context='')
        assert 'Prior Round Context' not in result

    def test_task_instructions_last(self) -> None:
        result = build_propose_prompt('ID', 'CO', 'FR')
        parts = result.split(_SEPARATOR)
        assert parts[-1].startswith('## Your Task')
        assert 'Summary, Code sketch' in parts[-1]

    def test_section_count_without_context(self) -> None:
        result = build_propose_prompt('ID', 'CO', 'FR')
        parts = result.split(_SEPARATOR)
        assert len(parts) == 4  # identity, codex, frame, task

    def test_section_count_with_context(self) -> None:
        result = build_propose_prompt('ID', 'CO', 'FR', prior_context='ctx')
        parts = result.split(_SEPARATOR)
        assert len(parts) == 5  # identity, codex, frame, context, task


# ---------------------------------------------------------------------------
# TestBuildDebatePrompt
# ---------------------------------------------------------------------------


class TestBuildDebatePrompt:
    def test_ordering(self) -> None:
        result = build_debate_prompt('IDENTITY', 'CODEX', 'FRAME', 'PROPOSALS')
        parts = result.split(_SEPARATOR)

        assert parts[0] == 'IDENTITY'
        assert parts[1] == 'CODEX'
        assert parts[2] == 'FRAME'
        assert parts[3] == 'PROPOSALS'

    def test_includes_proposals(self) -> None:
        result = build_debate_prompt('ID', 'CO', 'FR', 'all proposals here')
        assert 'all proposals here' in result

    def test_prior_context_when_provided(self) -> None:
        result = build_debate_prompt('ID', 'CO', 'FR', 'PROP', prior_context='prev')
        assert '## Prior Round Context' in result
        assert 'prev' in result

    def test_no_prior_context_by_default(self) -> None:
        result = build_debate_prompt('ID', 'CO', 'FR', 'PROP')
        assert 'Prior Round Context' not in result

    def test_task_instructions_last(self) -> None:
        result = build_debate_prompt('ID', 'CO', 'FR', 'PROP')
        parts = result.split(_SEPARATOR)
        assert parts[-1].startswith('## Your Task')
        assert 'My case' in parts[-1]

    def test_section_count_without_context(self) -> None:
        result = build_debate_prompt('ID', 'CO', 'FR', 'PROP')
        parts = result.split(_SEPARATOR)
        assert len(parts) == 5  # identity, codex, frame, proposals, task

    def test_section_count_with_context(self) -> None:
        result = build_debate_prompt('ID', 'CO', 'FR', 'PROP', prior_context='ctx')
        parts = result.split(_SEPARATOR)
        assert len(parts) == 6  # identity, codex, frame, proposals, context, task


# ---------------------------------------------------------------------------
# TestBuildVotePrompt
# ---------------------------------------------------------------------------


class TestBuildVotePrompt:
    def test_ordering(self) -> None:
        result = build_vote_prompt('IDENTITY', 'CODEX', 'PROPOSALS', 'DEBATE')
        parts = result.split(_SEPARATOR)

        assert parts[0] == 'IDENTITY'
        assert parts[1] == 'CODEX'
        assert parts[2] == 'PROPOSALS'
        assert parts[3] == 'DEBATE'

    def test_omits_task_framing(self) -> None:
        """Vote prompt must NOT include task framing per codex spec."""
        result = build_vote_prompt('IDENTITY', 'CODEX', 'PROPOSALS', 'DEBATE')
        parts = result.split(_SEPARATOR)
        # Only 5 parts: identity, codex, proposals, debate, task instructions
        assert len(parts) == 5

    def test_task_instructions_last(self) -> None:
        result = build_vote_prompt('ID', 'CO', 'PROP', 'DEB')
        parts = result.split(_SEPARATOR)
        assert parts[-1].startswith('## Your Task')
        assert 'Winner' in parts[-1]

    def test_includes_both_proposals_and_debate(self) -> None:
        result = build_vote_prompt('ID', 'CO', 'all proposals', 'all debate')
        assert 'all proposals' in result
        assert 'all debate' in result
