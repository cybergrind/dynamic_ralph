"""Tests for multi_agent.orchestrate — main orchestration loop."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent.backend import AgentResult
from multi_agent.orchestrate import (
    _enforce_quorum,
    _format_frame_text,
    _select_identities,
    run_multi_agent,
    validate_frame,
)
from multi_agent.tally import Frame


# ---------------------------------------------------------------------------
# Helpers for building canned agent responses
# ---------------------------------------------------------------------------

_PROPOSAL_TEMPLATE = """\
## Summary

Proposal {label}: a design that solves the problem with approach {label}.

## Code sketch

```python
def solution_{label}():
    return "{label}"
```

## Files changed

- src/main.py

## Migration plan

No migration needed.

## What I'd argue

This approach is simple and effective.

## What worries me

Scalability under heavy load.
"""

_DEBATE_TEMPLATE = """\
## My case

Proposal {label} is the best because it is simple.

## Challenges to other proposals

Other proposals are more complex without clear benefit.

## What I'd adopt from others

The error handling from proposal A.

## My biggest doubt

Whether simplicity holds at scale.
"""

_VOTE_TEMPLATE = """\
## Winner

Proposal {winner}

## Decisive argument

The simplicity argument was compelling.

## Concerns about the winner

Minor scalability concerns under heavy load, but acceptable for v1.
"""

_VOTE_WITH_VETO_TEMPLATE = """\
## Winner

Proposal {winner}

## Decisive argument

Best overall approach.

## Concerns about the winner

{winner}: critical security flaw in the authentication bypass logic
"""


def _make_result(text: str, *, exit_code: int = 0, timed_out: bool = False) -> AgentResult:
    return AgentResult(
        exit_code=exit_code,
        full_response=text,
        final_response=text,
        completion_status='end_turn' if exit_code == 0 else 'error',
        timed_out=timed_out,
    )


def _proposal_result(label: str) -> AgentResult:
    return _make_result(_PROPOSAL_TEMPLATE.format(label=label))


def _debate_result(label: str) -> AgentResult:
    return _make_result(_DEBATE_TEMPLATE.format(label=label))


def _vote_result(winner: str) -> AgentResult:
    return _make_result(_VOTE_TEMPLATE.format(winner=winner))


def _veto_vote_result(winner: str) -> AgentResult:
    return _make_result(_VOTE_WITH_VETO_TEMPLATE.format(winner=winner))


def _make_frame(**overrides) -> Frame:
    defaults = {
        'question': 'How should we design the API?',
        'scope_in': ['REST endpoints'],
        'scope_out': ['GraphQL'],
        'success_criteria': ['Clean API design'],
        'key_files': ['src/api.py'],
        'constraints': [],
        'identities': ['i_consul.md', 'i_architect.md', 'i_skeptic.md'],
    }
    defaults.update(overrides)
    return Frame(**defaults)


# ---------------------------------------------------------------------------
# Identities and texts used across tests
# ---------------------------------------------------------------------------

_TEST_IDENTITIES = ['i_consul.md', 'i_architect.md', 'i_skeptic.md', 'i_pragmatist.md', 'i_security.md']
_TEST_IDENTITY_TEXTS = {name: f'You are the {name} identity.' for name in _TEST_IDENTITIES}
_TEST_CODEX = 'Follow the multi-agent codex rules.'


# ---------------------------------------------------------------------------
# TestValidateFrame
# ---------------------------------------------------------------------------


class TestValidateFrame:
    def test_valid_frame_passes(self) -> None:
        frame = _make_frame()
        validate_frame(frame)  # should not raise

    def test_empty_question_raises(self) -> None:
        frame = _make_frame(question='')
        with pytest.raises(AssertionError, match='question must not be empty'):
            validate_frame(frame)

    def test_whitespace_question_raises(self) -> None:
        frame = _make_frame(question='   ')
        with pytest.raises(AssertionError, match='question must not be empty'):
            validate_frame(frame)

    def test_fewer_than_3_identities_raises(self) -> None:
        frame = _make_frame(identities=['a.md', 'b.md'])
        with pytest.raises(AssertionError, match='>= 3 identities'):
            validate_frame(frame)

    def test_empty_success_criteria_raises(self) -> None:
        frame = _make_frame(success_criteria=[])
        with pytest.raises(AssertionError, match='>= 1 success criterion'):
            validate_frame(frame)

    def test_empty_key_files_raises(self) -> None:
        frame = _make_frame(key_files=[])
        with pytest.raises(AssertionError, match='>= 1 key file'):
            validate_frame(frame)


# ---------------------------------------------------------------------------
# TestFormatFrameText
# ---------------------------------------------------------------------------


class TestFormatFrameText:
    def test_contains_question(self) -> None:
        frame = _make_frame()
        text = _format_frame_text(frame)
        assert '## Question' in text
        assert 'How should we design the API?' in text

    def test_contains_constraints(self) -> None:
        frame = _make_frame(constraints=['Must use REST', 'No breaking changes'])
        text = _format_frame_text(frame)
        assert '## Constraints' in text
        assert 'Must use REST' in text


# ---------------------------------------------------------------------------
# TestSelectIdentities
# ---------------------------------------------------------------------------


class TestSelectIdentities:
    def test_explicit_identities_returned(self) -> None:
        result = _select_identities(['a.md', 'b.md', 'c.md'], num_agents=3)
        assert result == ['a.md', 'b.md', 'c.md']

    def test_samples_from_directory(self, tmp_path: Path) -> None:
        for name in ['i_a.md', 'i_b.md', 'i_c.md', 'i_d.md', 'i_e.md']:
            (tmp_path / name).write_text(f'identity {name}')
        result = _select_identities(None, num_agents=3, base_path=tmp_path)
        assert len(result) == 3
        assert all(name.endswith('.md') for name in result)


# ---------------------------------------------------------------------------
# TestQuorumEnforcement
# ---------------------------------------------------------------------------


class TestQuorumEnforcement:
    def test_failed_agent_retried_once(self, tmp_path: Path) -> None:
        """One agent fails -> retried -> succeeds on retry -> quorum met."""
        results = {
            'A': _make_result('ok'),
            'B': _make_result('ok'),
            'C': _make_result('fail', exit_code=1),
        }
        prompts = {'A': 'p', 'B': 'p', 'C': 'p'}

        retry_results = {'C': _make_result('ok')}
        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=retry_results):
            merged = _enforce_quorum(
                results,
                prompts,
                backend=None,
                max_turns=3,
                timeout=300,
                log_dir=tmp_path,
            )

        assert merged['C'].exit_code == 0

    def test_below_quorum_after_retry_raises(self, tmp_path: Path) -> None:
        """3+ agents fail even after retry -> RuntimeError raised."""
        results = {
            'A': _make_result('ok'),
            'B': _make_result('fail', exit_code=1),
            'C': _make_result('fail', exit_code=1),
            'D': _make_result('fail', exit_code=1),
        }
        prompts = {'A': 'p', 'B': 'p', 'C': 'p', 'D': 'p'}

        # Retry also fails
        retry_results = {
            'B': _make_result('fail', exit_code=1),
            'C': _make_result('fail', exit_code=1),
            'D': _make_result('fail', exit_code=1),
        }
        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=retry_results):
            with pytest.raises(RuntimeError, match='Quorum not met'):
                _enforce_quorum(
                    results,
                    prompts,
                    backend=None,
                    max_turns=3,
                    timeout=300,
                    log_dir=tmp_path,
                )

    def test_no_retry_when_all_succeed(self, tmp_path: Path) -> None:
        """All agents succeed -> no retry, no launch_parallel_agents call."""
        results = {
            'A': _make_result('ok'),
            'B': _make_result('ok'),
            'C': _make_result('ok'),
        }
        prompts = {'A': 'p', 'B': 'p', 'C': 'p'}

        with patch('multi_agent.orchestrate.launch_parallel_agents') as mock_launch:
            merged = _enforce_quorum(
                results,
                prompts,
                backend=None,
                max_turns=3,
                timeout=300,
                log_dir=tmp_path,
            )
            mock_launch.assert_not_called()

        assert len(merged) == 3


# ---------------------------------------------------------------------------
# TestRunMultiAgent — integration tests with mocked launch_parallel_agents
# ---------------------------------------------------------------------------


def _build_launch_side_effect(rounds: list[dict]) -> list[dict[str, AgentResult]]:
    """Build a side_effect list for launch_parallel_agents.

    Each round has 3 calls (propose, debate, vote).
    *rounds* is a list of dicts with keys 'propose', 'debate', 'vote',
    each mapping labels to AgentResult.
    """
    effects = []
    for r in rounds:
        effects.append(r['propose'])
        effects.append(r['debate'])
        effects.append(r['vote'])
    return effects


class TestRunMultiAgent:
    """Integration tests with mocked launch_parallel_agents."""

    def test_strong_consensus_decides_in_one_round(self, tmp_path: Path) -> None:
        """All 5 agents vote for same proposal -> strong consensus."""
        labels = ['A', 'B', 'C', 'D', 'E']
        propose_results = {lbl: _proposal_result(lbl) for lbl in labels}
        debate_results = {lbl: _debate_result(lbl) for lbl in labels}
        vote_results = {lbl: _vote_result('A') for lbl in labels}

        effects = [propose_results, debate_results, vote_results]

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects):
            decision = run_multi_agent(
                'How should we design the API?',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        assert decision.winner == 'A'
        assert decision.consensus_type == 'strong'
        assert 'Proposal A' in decision.decision_text

    def test_split_vote_iterates_with_binary_constraint(self, tmp_path: Path) -> None:
        """No majority in round 1 -> adds binary choice constraint -> round 2 converges."""
        labels = ['A', 'B', 'C', 'D', 'E']

        # Round 1: split vote (A=2, B=2, C=1)
        split_votes = {
            'A': _vote_result('A'),
            'B': _vote_result('A'),
            'C': _vote_result('B'),
            'D': _vote_result('B'),
            'E': _vote_result('C'),
        }

        # Round 2: consensus on A
        consensus_votes = {lbl: _vote_result('A') for lbl in labels}

        round1 = {
            'propose': {lbl: _proposal_result(lbl) for lbl in labels},
            'debate': {lbl: _debate_result(lbl) for lbl in labels},
            'vote': split_votes,
        }
        round2 = {
            'propose': {lbl: _proposal_result(lbl) for lbl in labels},
            'debate': {lbl: _debate_result(lbl) for lbl in labels},
            'vote': consensus_votes,
        }

        effects = _build_launch_side_effect([round1, round2])

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects):
            decision = run_multi_agent(
                'How should we design the API?',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        assert decision.winner == 'A'
        assert decision.consensus_type == 'strong'

    def test_veto_iterates_with_flaw_constraint(self, tmp_path: Path) -> None:
        """3+ agents cite same flaw -> veto -> flaw added as constraint -> round 2 converges."""
        labels = ['A', 'B', 'C', 'D', 'E']

        # Round 1: everyone votes A, but 3+ share the same concern -> veto
        veto_votes = {lbl: _veto_vote_result('A') for lbl in labels}

        # Round 2: consensus on A (concerns addressed)
        consensus_votes = {lbl: _vote_result('A') for lbl in labels}

        round1 = {
            'propose': {lbl: _proposal_result(lbl) for lbl in labels},
            'debate': {lbl: _debate_result(lbl) for lbl in labels},
            'vote': veto_votes,
        }
        round2 = {
            'propose': {lbl: _proposal_result(lbl) for lbl in labels},
            'debate': {lbl: _debate_result(lbl) for lbl in labels},
            'vote': consensus_votes,
        }

        effects = _build_launch_side_effect([round1, round2])

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects):
            decision = run_multi_agent(
                'How should we design the API?',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        assert decision.winner == 'A'
        assert decision.consensus_type == 'strong'

    def test_max_rounds_escalation(self, tmp_path: Path) -> None:
        """Perpetual split -> max_rounds reached -> DecisionRecord with consensus_type='escalated'."""
        labels = ['A', 'B', 'C', 'D', 'E']

        # Every round is a split (A=2, B=2, C=1)
        split_votes = {
            'A': _vote_result('A'),
            'B': _vote_result('A'),
            'C': _vote_result('B'),
            'D': _vote_result('B'),
            'E': _vote_result('C'),
        }

        round_data = {
            'propose': {lbl: _proposal_result(lbl) for lbl in labels},
            'debate': {lbl: _debate_result(lbl) for lbl in labels},
            'vote': split_votes,
        }

        effects = _build_launch_side_effect([round_data, round_data, round_data])

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects):
            decision = run_multi_agent(
                'How should we design the API?',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        assert decision.consensus_type == 'escalated'
        assert 'escalated' in decision.decision_text

    def test_artifacts_written_to_disk(self, tmp_path: Path) -> None:
        """Verify metadata.json, framing.md, tally.md, decision.md are created."""
        labels = ['A', 'B', 'C', 'D', 'E']
        effects = [
            {lbl: _proposal_result(lbl) for lbl in labels},
            {lbl: _debate_result(lbl) for lbl in labels},
            {lbl: _vote_result('A') for lbl in labels},
        ]

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects):
            run_multi_agent(
                'Test question',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        # Find the run directory (contains the run_id)
        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]

        assert (run_dir / 'metadata.json').exists()
        assert (run_dir / 'framing.md').exists()
        assert (run_dir / 'decision.md').exists()
        assert (run_dir / 'round-1' / 'tally.md').exists()
        assert (run_dir / 'round-1' / 'proposals').is_dir()
        assert (run_dir / 'round-1' / 'debate').is_dir()
        assert (run_dir / 'round-1' / 'votes').is_dir()
