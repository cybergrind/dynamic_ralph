"""Tests for multi_agent.tally module."""

from __future__ import annotations

from pathlib import Path

from multi_agent.parsing import VoteResult
from multi_agent.tally import (
    DecisionRecord,
    Frame,
    Tally,
    _cluster_concerns,
    build_decision,
    build_iteration_context,
    compute_tally,
    detect_veto,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vote(
    label: str,
    winner: str,
    *,
    concerns: dict[str, str] | None = None,
    unrefuted: list[str] | None = None,
    merge: str | None = None,
) -> VoteResult:
    return VoteResult(
        voter_label=label,
        winner=winner,
        decisive_argument=f'{label} found {winner} compelling',
        concerns=concerns or {},
        unrefuted_arguments=unrefuted or [],
        merge_suggestion=merge,
    )


def _frame() -> Frame:
    return Frame(
        question='How should we implement caching?',
        scope_in=['api layer'],
        scope_out=['frontend'],
        success_criteria=['latency < 100ms'],
        key_files=['src/api.py'],
        constraints=['no external deps'],
        identities=['A', 'B', 'C'],
    )


# ---------------------------------------------------------------------------
# Tally dataclass
# ---------------------------------------------------------------------------


class TestTallyDataclass:
    def test_no_override_candidate_field(self) -> None:
        """Tally dataclass does NOT have override_candidate field."""
        t = Tally(
            total_votes=5,
            vote_counts={'A': 5},
            winner='A',
            winner_pct=100.0,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='strong',
            vetoed=False,
        )
        assert not hasattr(t, 'override_candidate')

    def test_has_consensus_strong(self) -> None:
        t = Tally(
            total_votes=5,
            vote_counts={'A': 5},
            winner='A',
            winner_pct=100.0,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='strong',
            vetoed=False,
        )
        assert t.has_consensus() is True

    def test_has_consensus_majority(self) -> None:
        t = Tally(
            total_votes=5,
            vote_counts={'A': 3, 'B': 2},
            winner='A',
            winner_pct=60.0,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='majority',
            vetoed=False,
        )
        assert t.has_consensus() is True

    def test_has_consensus_split_returns_false(self) -> None:
        t = Tally(
            total_votes=5,
            vote_counts={'A': 2, 'B': 2, 'C': 1},
            winner='A',
            winner_pct=40.0,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='split',
            vetoed=False,
        )
        assert t.has_consensus() is False

    def test_has_consensus_vetoed_returns_false(self) -> None:
        t = Tally(
            total_votes=5,
            vote_counts={'A': 5},
            winner='A',
            winner_pct=100.0,
            veto_flaws={'A': ['data loss concern']},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='strong',
            vetoed=True,
        )
        assert t.has_consensus() is False


# ---------------------------------------------------------------------------
# compute_tally
# ---------------------------------------------------------------------------


class TestComputeTally:
    def test_unanimous(self) -> None:
        """All 5 voters pick the same proposal -> strong consensus."""
        votes = {label: _vote(label, 'A') for label in ['V1', 'V2', 'V3', 'V4', 'V5']}
        tally = compute_tally(votes, {'A': 'proposal A', 'B': 'proposal B'})
        assert tally.winner == 'A'
        assert tally.winner_pct == 100.0
        assert tally.consensus_type == 'strong'
        assert tally.total_votes == 5
        assert tally.has_consensus() is True

    def test_supermajority(self) -> None:
        """4 out of 5 voters (80%) -> strong consensus."""
        votes = {
            'V1': _vote('V1', 'A'),
            'V2': _vote('V2', 'A'),
            'V3': _vote('V3', 'A'),
            'V4': _vote('V4', 'A'),
            'V5': _vote('V5', 'B'),
        }
        tally = compute_tally(votes, {'A': 'proposal A', 'B': 'proposal B'})
        assert tally.winner == 'A'
        assert tally.winner_pct == 80.0
        assert tally.consensus_type == 'strong'

    def test_majority(self) -> None:
        """3 out of 5 voters (60%) -> majority consensus."""
        votes = {
            'V1': _vote('V1', 'A'),
            'V2': _vote('V2', 'A'),
            'V3': _vote('V3', 'A'),
            'V4': _vote('V4', 'B'),
            'V5': _vote('V5', 'B'),
        }
        tally = compute_tally(votes, {'A': 'proposal A', 'B': 'proposal B'})
        assert tally.winner == 'A'
        assert tally.winner_pct == 60.0
        assert tally.consensus_type == 'majority'
        assert tally.has_consensus() is True

    def test_split(self) -> None:
        """2 out of 5 voters (40%) -> split, no consensus."""
        votes = {
            'V1': _vote('V1', 'A'),
            'V2': _vote('V2', 'A'),
            'V3': _vote('V3', 'B'),
            'V4': _vote('V4', 'B'),
            'V5': _vote('V5', 'C'),
        }
        tally = compute_tally(votes, {'A': 'prop A', 'B': 'prop B', 'C': 'prop C'})
        assert tally.winner in ('A', 'B')  # tie at 40%, max picks first
        assert tally.winner_pct == 40.0
        assert tally.consensus_type == 'split'
        assert tally.has_consensus() is False

    def test_tie(self) -> None:
        """Tie between two proposals -> split."""
        votes = {
            'V1': _vote('V1', 'A'),
            'V2': _vote('V2', 'A'),
            'V3': _vote('V3', 'B'),
            'V4': _vote('V4', 'B'),
        }
        tally = compute_tally(votes, {'A': 'prop A', 'B': 'prop B'})
        assert tally.winner_pct == 50.0
        assert tally.consensus_type == 'majority'

    def test_exactly_70_pct_is_strong(self) -> None:
        """70% boundary -> strong consensus."""
        votes = {f'V{i}': _vote(f'V{i}', 'A') for i in range(1, 8)}
        for i in range(8, 11):
            votes[f'V{i}'] = _vote(f'V{i}', 'B')
        tally = compute_tally(votes, {'A': 'prop A', 'B': 'prop B'})
        assert tally.winner_pct == 70.0
        assert tally.consensus_type == 'strong'

    def test_collects_unrefuted_args(self) -> None:
        votes = {
            'V1': _vote('V1', 'A', unrefuted=['arg1']),
            'V2': _vote('V2', 'A', unrefuted=['arg2', 'arg3']),
            'V3': _vote('V3', 'A'),
        }
        tally = compute_tally(votes, {'A': 'prop A'})
        assert tally.unrefuted_args == ['arg1', 'arg2', 'arg3']

    def test_collects_merge_suggestions(self) -> None:
        votes = {
            'V1': _vote('V1', 'A', merge='merge A and B'),
            'V2': _vote('V2', 'A', merge='combine approaches'),
            'V3': _vote('V3', 'A'),
        }
        tally = compute_tally(votes, {'A': 'prop A'})
        assert tally.merge_suggestions == ['merge A and B', 'combine approaches']


# ---------------------------------------------------------------------------
# Veto detection
# ---------------------------------------------------------------------------


class TestClusterConcerns:
    def test_similar_concerns_cluster_together(self) -> None:
        concerns = [
            'data loss during migration',
            'data loss from migration',
            'data loss in migration',
        ]
        clusters = _cluster_concerns(concerns)
        assert len(clusters) == 1

    def test_unrelated_concerns_separate_clusters(self) -> None:
        concerns = [
            'data loss during migration',
            'performance regression under load',
            'security vulnerability in auth',
        ]
        clusters = _cluster_concerns(concerns)
        assert len(clusters) == 3

    def test_empty_list(self) -> None:
        clusters = _cluster_concerns([])
        assert clusters == []

    def test_single_concern(self) -> None:
        clusters = _cluster_concerns(['one concern'])
        assert len(clusters) == 1
        assert clusters[0] == ['one concern']


class TestDetectVeto:
    def test_veto_fires_on_clustered_concerns(self) -> None:
        """3+ similar concerns about winner -> veto fires."""
        all_concerns: dict[str, list[str]] = {
            'A': [
                'data loss during migration',
                'data loss from migration',
                'data loss in migration',
            ],
        }
        vetoed, flaws = detect_veto('A', all_concerns)
        assert vetoed is True
        assert 'A' in flaws
        assert len(flaws['A']) == 3

    def test_no_veto_on_unrelated_concerns(self) -> None:
        """3 unrelated concerns about winner -> veto does NOT fire."""
        all_concerns: dict[str, list[str]] = {
            'A': [
                'data loss during migration',
                'performance regression under load',
                'security vulnerability in auth module',
            ],
        }
        vetoed, flaws = detect_veto('A', all_concerns)
        assert vetoed is False
        assert flaws == {}

    def test_no_veto_below_threshold(self) -> None:
        """Only 2 similar concerns -> below threshold, no veto."""
        all_concerns: dict[str, list[str]] = {
            'A': [
                'data loss during migration',
                'data loss in migration process',
            ],
        }
        vetoed, flaws = detect_veto('A', all_concerns)
        assert vetoed is False
        assert flaws == {}

    def test_no_veto_when_no_concerns(self) -> None:
        vetoed, flaws = detect_veto('A', {})
        assert vetoed is False
        assert flaws == {}

    def test_concerns_about_other_proposal_dont_veto_winner(self) -> None:
        """Concerns about proposal B don't trigger veto on winner A."""
        all_concerns: dict[str, list[str]] = {
            'B': [
                'data loss during migration step',
                'data loss in the migration process',
                'migration causes data loss risk',
            ],
        }
        vetoed, _flaws = detect_veto('A', all_concerns)
        assert vetoed is False

    def test_custom_veto_threshold(self) -> None:
        all_concerns: dict[str, list[str]] = {
            'A': [
                'data loss during migration',
                'data loss in migration process',
            ],
        }
        vetoed, _flaws = detect_veto('A', all_concerns, veto_threshold=2)
        assert vetoed is True

    def test_veto_integrated_with_compute_tally(self) -> None:
        """Veto through compute_tally: unanimous vote vetoed by clustered concerns."""
        concern_text = 'data loss during migration'
        votes = {
            f'V{i}': _vote(
                f'V{i}',
                'A',
                concerns={'A': f'{concern_text} step {i}' if i <= 2 else concern_text},
            )
            for i in range(1, 6)
        }
        # Make all concerns similar enough to cluster
        for v in votes.values():
            v.concerns = {'A': 'data loss during migration'}
        tally = compute_tally(votes, {'A': 'prop A', 'B': 'prop B'})
        assert tally.vetoed is True
        assert tally.has_consensus() is False


# ---------------------------------------------------------------------------
# build_decision
# ---------------------------------------------------------------------------


class TestBuildDecision:
    def test_basic_decision(self, tmp_path: Path) -> None:
        tally = Tally(
            total_votes=5,
            vote_counts={'A': 4, 'B': 1},
            winner='A',
            winner_pct=80.0,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='strong',
            vetoed=False,
        )
        record = build_decision(_frame(), tally, {'A': 'prop A'}, {'A': 'debate A'}, tmp_path)
        assert isinstance(record, DecisionRecord)
        assert record.winner == 'A'
        assert record.consensus_type == 'strong'
        assert record.override_applied is False
        assert 'Proposal A adopted' in record.decision_text

    def test_v1_override_always_false(self, tmp_path: Path) -> None:
        """v1 _check_override returns False, logs unrefuted args."""
        tally = Tally(
            total_votes=5,
            vote_counts={'A': 4, 'B': 1},
            winner='A',
            winner_pct=80.0,
            veto_flaws={},
            unrefuted_args=['critical flaw in A'],
            merge_suggestions=[],
            consensus_type='strong',
            vetoed=False,
        )
        record = build_decision(_frame(), tally, {'A': 'prop A'}, {'A': 'debate A'}, tmp_path)
        assert record.override_applied is False
        assert record.unrefuted_args_for_review == ['critical flaw in A']


# ---------------------------------------------------------------------------
# build_iteration_context
# ---------------------------------------------------------------------------


class TestBuildIterationContext:
    def test_includes_tally_info(self) -> None:
        tally = Tally(
            total_votes=5,
            vote_counts={'A': 2, 'B': 2, 'C': 1},
            winner='A',
            winner_pct=40.0,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='split',
            vetoed=False,
        )
        ctx = build_iteration_context(
            1,
            {'A': 'prop A', 'B': 'prop B'},
            {'A': 'debate A', 'B': 'debate B'},
            {'V1': _vote('V1', 'A')},
            tally,
        )
        assert 'Round 1 Results' in ctx
        assert 'Winner: Proposal A (40%)' in ctx
        assert 'Consensus: split' in ctx

    def test_includes_veto_info(self) -> None:
        tally = Tally(
            total_votes=5,
            vote_counts={'A': 5},
            winner='A',
            winner_pct=100.0,
            veto_flaws={'A': ['data loss concern', 'migration risk', 'data integrity']},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='strong',
            vetoed=True,
        )
        ctx = build_iteration_context(1, {'A': 'prop A'}, {'A': 'debate A'}, {}, tally)
        assert 'VETOED' in ctx
        assert 'data loss concern' in ctx

    def test_includes_unrefuted_args(self) -> None:
        tally = Tally(
            total_votes=3,
            vote_counts={'A': 2, 'B': 1},
            winner='A',
            winner_pct=66.7,
            veto_flaws={},
            unrefuted_args=['arg1', 'arg2'],
            merge_suggestions=[],
            consensus_type='majority',
            vetoed=False,
        )
        ctx = build_iteration_context(1, {'A': 'prop A'}, {}, {}, tally)
        assert 'Unrefuted Arguments' in ctx
        assert '- arg1' in ctx
        assert '- arg2' in ctx

    def test_includes_merge_suggestions(self) -> None:
        tally = Tally(
            total_votes=3,
            vote_counts={'A': 2, 'B': 1},
            winner='A',
            winner_pct=66.7,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=['combine A and B approaches'],
            consensus_type='majority',
            vetoed=False,
        )
        ctx = build_iteration_context(1, {'A': 'prop A'}, {}, {}, tally)
        assert 'Merge Suggestions' in ctx
        assert 'combine A and B approaches' in ctx

    def test_includes_prior_proposals_and_debate(self) -> None:
        tally = Tally(
            total_votes=3,
            vote_counts={'A': 2, 'B': 1},
            winner='A',
            winner_pct=66.7,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='majority',
            vetoed=False,
        )
        ctx = build_iteration_context(
            2,
            {'A': 'implementation plan A', 'B': 'implementation plan B'},
            {'A': 'debate from A', 'B': 'debate from B'},
            {},
            tally,
        )
        assert 'Prior Proposals' in ctx
        assert 'Proposal A' in ctx
        assert 'implementation plan A' in ctx
        assert 'Prior Debate' in ctx
        assert "A's Debate Entry" in ctx
        assert 'debate from A' in ctx

    def test_includes_iteration_instructions(self) -> None:
        tally = Tally(
            total_votes=3,
            vote_counts={'A': 2, 'B': 1},
            winner='A',
            winner_pct=66.7,
            veto_flaws={},
            unrefuted_args=[],
            merge_suggestions=[],
            consensus_type='majority',
            vetoed=False,
        )
        ctx = build_iteration_context(1, {'A': 'prop A'}, {}, {}, tally)
        assert 'Instructions for This Round' in ctx
        assert 'REVISED proposal' in ctx
