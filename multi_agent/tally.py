"""Vote tally computation, veto detection, and decision logic.

Computes facts from vote results (counts, consensus type, veto) and builds
decision records. Override evaluation is deliberately separated from tally
computation: the tally computes facts, while override requires debate context
and is evaluated in build_decision().

Key design choices:
- Jaccard word-overlap clustering for veto detection (deterministic, no LLM)
- Override deferred to build_decision() where debate context is available
- v1 _check_override() returns False, logs unrefuted args for human review
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from multi_agent.parsing import VoteResult


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    """Structured problem frame for multi-agent deliberation."""

    question: str
    scope_in: list[str]
    scope_out: list[str]
    success_criteria: list[str]
    key_files: list[str]
    constraints: list[str]
    identities: list[str]


@dataclass
class Tally:
    """Result of tallying votes from a single round.

    Override evaluation is deliberately excluded from the tally. The tally
    computes facts (vote counts, veto detection). Override requires debate
    context to determine whether an unrefuted argument actually contradicts
    the winner -- that judgment belongs in build_decision(), not here.
    """

    total_votes: int
    vote_counts: dict[str, int]  # proposal_label -> count
    winner: str  # proposal label with most votes
    winner_pct: float  # percentage of votes for winner
    veto_flaws: dict[str, list[str]]  # proposal_label -> list of shared concerns
    unrefuted_args: list[str]  # arguments no voter countered
    merge_suggestions: list[str]  # voter-proposed hybrids
    consensus_type: str = ''  # "strong", "majority", "split"
    vetoed: bool = False

    def has_consensus(self) -> bool:
        """True if the tally indicates we should proceed to DECIDE.

        Note: override is evaluated separately in build_decision() where
        debate context is available to determine if unrefuted arguments
        actually contradict the winner.
        """
        if self.vetoed:
            return False
        return self.consensus_type in ('strong', 'majority')


@dataclass
class DecisionRecord:
    """Output from build_decision() capturing the final decision."""

    winner: str  # proposal label adopted
    consensus_type: str  # "strong", "majority", "split", "forced"
    tally: Tally
    override_applied: bool = False
    override_reason: str = ''
    unrefuted_args_for_review: list[str] = field(default_factory=list)
    decision_text: str = ''  # human-readable decision summary


# ---------------------------------------------------------------------------
# Jaccard clustering for veto detection
# ---------------------------------------------------------------------------


def _cluster_concerns(
    concerns: list[str],
    *,
    threshold: float = 0.5,
) -> list[list[str]]:
    """Group concern strings by Jaccard word-overlap similarity.

    Two concerns land in the same cluster if their word overlap exceeds
    *threshold*. Deliberately simple -- no embeddings, no LLM call --
    because veto detection must be deterministic and fast.
    """
    word_sets = [set(c.lower().split()) for c in concerns]
    clusters: list[list[int]] = []

    for i, ws_i in enumerate(word_sets):
        placed = False
        for cluster in clusters:
            ws_rep = word_sets[cluster[0]]
            intersection = len(ws_i & ws_rep)
            union = len(ws_i | ws_rep)
            if union > 0 and intersection / union >= threshold:
                cluster.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])

    return [[concerns[i] for i in cluster] for cluster in clusters]


# ---------------------------------------------------------------------------
# Veto detection
# ---------------------------------------------------------------------------


def detect_veto(
    winner: str,
    all_concerns: dict[str, list[str]],
    *,
    veto_threshold: int = 3,
) -> tuple[bool, dict[str, list[str]]]:
    """Return (vetoed, veto_flaws) for the winning proposal.

    A veto fires only when a single *cluster* of semantically similar
    concerns about the winner has >= veto_threshold members.
    """
    winner_concerns = all_concerns.get(winner, [])
    if len(winner_concerns) < veto_threshold:
        return False, {}

    clusters = _cluster_concerns(winner_concerns)
    veto_clusters = [c for c in clusters if len(c) >= veto_threshold]

    if veto_clusters:
        return True, {winner: veto_clusters[0]}
    return False, {}


# ---------------------------------------------------------------------------
# Tally computation
# ---------------------------------------------------------------------------


def compute_tally(
    votes: dict[str, VoteResult],
    proposals: dict[str, str],
) -> Tally:
    """Tally votes and determine consensus type.

    Does NOT evaluate override. Override requires debate context to determine
    whether unrefuted arguments contradict the winner. That evaluation happens
    in build_decision(). Unrefuted arguments are collected here for downstream
    use.
    """
    total = len(votes)
    counts: dict[str, int] = {}
    concerns: dict[str, list[str]] = {}

    for vote in votes.values():
        counts[vote.winner] = counts.get(vote.winner, 0) + 1

        # Track concerns for veto detection
        for proposal_label, concern in vote.concerns.items():
            concerns.setdefault(proposal_label, []).append(concern)

    # Find winner
    winner = max(counts, key=lambda k: counts[k])
    winner_pct = counts[winner] / total * 100

    # Consensus classification
    if winner_pct >= 70:
        consensus_type = 'strong'
    elif winner_pct >= 50:
        consensus_type = 'majority'
    else:
        consensus_type = 'split'

    # Veto check: use semantic clustering, not raw count
    vetoed, veto_flaws = detect_veto(winner, concerns)

    # Collect unrefuted arguments and merge suggestions (for build_decision())
    unrefuted = [arg for v in votes.values() for arg in (v.unrefuted_arguments or [])]
    merges = [v.merge_suggestion for v in votes.values() if v.merge_suggestion]

    return Tally(
        total_votes=total,
        vote_counts=counts,
        winner=winner,
        winner_pct=winner_pct,
        veto_flaws=veto_flaws,
        unrefuted_args=unrefuted,
        merge_suggestions=merges,
        consensus_type=consensus_type,
        vetoed=vetoed,
    )


# ---------------------------------------------------------------------------
# Override check (v1 stub)
# ---------------------------------------------------------------------------


def _check_override(
    tally: Tally,
    debate_entries: dict[str, str],
) -> tuple[bool, str]:
    """Check if an unrefuted argument should override the winner.

    For v1, returns False and logs unrefuted arguments for human review.
    The codex says "the operator makes the final call" -- this respects
    that by making the human the override arbiter rather than a broken
    heuristic.
    """
    if tally.unrefuted_args:
        log.info(
            'Unrefuted arguments for human review: %s',
            tally.unrefuted_args,
        )
    return False, ''


# ---------------------------------------------------------------------------
# Decision builder
# ---------------------------------------------------------------------------


def build_decision(
    frame: Frame,
    tally: Tally,
    proposals: dict[str, str],
    debate_entries: dict[str, str],
    work_dir: Path,
) -> DecisionRecord:
    """Build a decision record from the tally and debate context.

    Evaluates override here where debate context is available. For v1,
    _check_override() returns False and logs unrefuted arguments for
    human review.
    """
    # Evaluate override in context (v1: always False)
    override_applied, override_reason = _check_override(tally, debate_entries)

    return DecisionRecord(
        winner=tally.winner,
        consensus_type=tally.consensus_type,
        tally=tally,
        override_applied=override_applied,
        override_reason=override_reason,
        unrefuted_args_for_review=list(tally.unrefuted_args),
        decision_text=(f'Proposal {tally.winner} adopted ({tally.consensus_type}, {tally.winner_pct:.0f}% support).'),
    )


# ---------------------------------------------------------------------------
# Iteration context builder
# ---------------------------------------------------------------------------


def build_iteration_context(
    round_num: int,
    proposals: dict[str, str],
    debate_entries: dict[str, str],
    votes: dict[str, VoteResult],
    tally: Tally,
) -> str:
    """Build context string for the next iteration round."""
    parts = [
        f'## Round {round_num} Results\n',
        f'### Tally\nWinner: Proposal {tally.winner} ({tally.winner_pct:.0f}%)\nConsensus: {tally.consensus_type}\n',
    ]

    if tally.vetoed:
        parts.append(
            f'### VETOED\n'
            f'Proposal {tally.winner} was vetoed. Flaws cited by 3+ voters:\n'
            + '\n'.join(f'- {f}' for f in tally.veto_flaws.get(tally.winner, []))
        )

    if tally.unrefuted_args:
        parts.append('### Unrefuted Arguments\n' + '\n'.join(f'- {a}' for a in tally.unrefuted_args))

    if tally.merge_suggestions:
        parts.append('### Merge Suggestions\n' + '\n'.join(f'- {s}' for s in tally.merge_suggestions))

    # Include all proposals and debate from the prior round
    parts.append('### Prior Proposals\n')
    for label, text in sorted(proposals.items()):
        parts.append(f'#### Proposal {label}\n{text}\n')

    parts.append('### Prior Debate\n')
    for label, text in sorted(debate_entries.items()):
        parts.append(f"#### {label}'s Debate Entry\n{text}\n")

    # Instructions for the next round
    parts.append(
        '### Instructions for This Round\n'
        "You have seen the prior round's proposals, debate, and votes. "
        'The framing has been tightened based on the results. '
        'Produce a REVISED proposal that addresses the concerns raised. '
        'You may adopt elements from other proposals.'
    )

    return '\n\n'.join(parts)
