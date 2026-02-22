"""Main orchestration loop: FRAME -> PROPOSE -> DEBATE -> VOTE -> DECIDE.

Wires together parallel execution, prompt composition, parsing, and tally
into a cyclic multi-agent deliberation process with convergence detection,
quorum enforcement, and escalation.
"""

from __future__ import annotations

import json
import logging
import random
import string
from datetime import datetime, timezone
from pathlib import Path

from multi_agent.backend import AgentBackend, AgentResult
from multi_agent.codex_prompts import (
    IDENTITIES_DIR,
    build_debate_prompt,
    build_propose_prompt,
    build_vote_prompt,
    concatenate_debate,
    concatenate_proposals,
    load_codex,
    load_identity,
)
from multi_agent.parallel import launch_parallel_agents
from multi_agent.parsing import (
    VoteResult,
    parse_proposal,
    parse_vote,
    summarize_phase_health,
    write_phase_diagnostics,
)
from multi_agent.tally import (
    DecisionRecord,
    Frame,
    Tally,
    build_decision,
    build_iteration_context,
    compute_tally,
)


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase configuration
# ---------------------------------------------------------------------------

PROPOSE_TIMEOUT = 900
PROPOSE_MAX_TURNS = 10
DEBATE_TIMEOUT = 600
DEBATE_MAX_TURNS = 5
VOTE_TIMEOUT = 300
VOTE_MAX_TURNS = 3
QUORUM_MIN = 3

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_frame(frame: Frame) -> None:
    """Pre-flight check before launching expensive agent calls.

    Raises AssertionError on malformed frame. Validates:
    - Non-empty question
    - >= 3 identities
    - >= 1 success criterion
    - >= 1 key file
    """
    assert frame.question.strip(), 'Frame question must not be empty'
    assert len(frame.identities) >= 3, f'Frame requires >= 3 identities, got {len(frame.identities)}'
    assert len(frame.success_criteria) >= 1, 'Frame requires >= 1 success criterion'
    assert len(frame.key_files) >= 1, 'Frame requires >= 1 key file'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_id() -> str:
    """Generate run ID like '20260219T143022_a1b2c3d4'."""
    ts = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')
    suffix = ''.join(random.choices('0123456789abcdef', k=8))
    return f'{ts}_{suffix}'


def _format_frame_text(frame: Frame) -> str:
    """Render Frame as markdown for inclusion in agent prompts."""
    parts = [f'## Question\n\n{frame.question}']

    if frame.scope_in:
        parts.append('## In Scope\n\n' + '\n'.join(f'- {s}' for s in frame.scope_in))
    if frame.scope_out:
        parts.append('## Out of Scope\n\n' + '\n'.join(f'- {s}' for s in frame.scope_out))
    if frame.success_criteria:
        parts.append('## Success Criteria\n\n' + '\n'.join(f'- {c}' for c in frame.success_criteria))
    if frame.key_files:
        parts.append('## Key Files\n\n' + '\n'.join(f'- {f}' for f in frame.key_files))
    if frame.constraints:
        parts.append('## Constraints\n\n' + '\n'.join(f'- {c}' for c in frame.constraints))

    return '\n\n'.join(parts)


def _select_identities(
    identities: list[str] | None,
    num_agents: int,
    base_path: Path | None = None,
) -> list[str]:
    """Pick identity filenames.

    Use provided list or random sample from IDENTITIES_DIR.
    """
    if identities is not None:
        return list(identities)

    directory = base_path or IDENTITIES_DIR
    available = [f.name for f in directory.iterdir() if f.suffix == '.md']
    if len(available) < num_agents:
        return available
    return random.sample(available, num_agents)


def _assign_labels(identity_names: list[str]) -> dict[str, str]:
    """Map identity filenames to uppercase labels (A, B, C, ...)."""
    return {identity: string.ascii_uppercase[i] for i, identity in enumerate(identity_names)}


# ---------------------------------------------------------------------------
# Quorum enforcement
# ---------------------------------------------------------------------------


def _enforce_quorum(
    results: dict[str, AgentResult],
    prompts: dict[str, str],
    *,
    backend: AgentBackend | None,
    max_turns: int,
    timeout: int,
    log_dir: Path,
) -> dict[str, AgentResult]:
    """Retry failed agents once. Raise RuntimeError if still below QUORUM_MIN."""
    failed = {label: prompts[label] for label, r in results.items() if r.exit_code != 0 or r.timed_out}

    if not failed:
        return results

    log.info('Retrying %d failed agents: %s', len(failed), list(failed.keys()))
    retry_results = launch_parallel_agents(
        failed,
        backend=backend,
        max_turns=max_turns,
        timeout=timeout,
        log_dir=log_dir / 'retry',
    )

    merged = dict(results)
    for label, result in retry_results.items():
        if result.exit_code == 0 and not result.timed_out:
            merged[label] = result

    succeeded = sum(1 for r in merged.values() if r.exit_code == 0 and not r.timed_out)
    if succeeded < QUORUM_MIN:
        msg = f'Quorum not met: {succeeded}/{len(merged)} agents succeeded (need {QUORUM_MIN})'
        raise RuntimeError(msg)

    return merged


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------


def run_propose(
    frame: Frame,
    identity_texts: dict[str, str],
    codex_text: str,
    frame_text: str,
    prior_context: str | None,
    round_dir: Path,
    log_dir: Path,
    *,
    backend: AgentBackend | None = None,
) -> dict[str, str]:
    """Build per-agent PROPOSE prompts, launch in parallel, parse proposals.

    Returns proposals dict keyed by uppercase label (A, B, C, ...).
    Writes agent-{label}.md and all-proposals.md to round_dir/proposals/.
    """
    label_map = _assign_labels(list(identity_texts.keys()))
    prompts: dict[str, str] = {}
    for identity, label in label_map.items():
        prompts[label] = build_propose_prompt(
            identity_text=identity_texts[identity],
            codex_text=codex_text,
            frame_text=frame_text,
            prior_context=prior_context,
        )

    results = launch_parallel_agents(
        prompts,
        backend=backend,
        max_turns=PROPOSE_MAX_TURNS,
        timeout=PROPOSE_TIMEOUT,
        log_dir=log_dir / 'propose',
    )
    results = _enforce_quorum(
        results,
        prompts,
        backend=backend,
        max_turns=PROPOSE_MAX_TURNS,
        timeout=PROPOSE_TIMEOUT,
        log_dir=log_dir / 'propose',
    )

    proposals_dir = round_dir / 'proposals'
    proposals_dir.mkdir(parents=True, exist_ok=True)

    proposals: dict[str, str] = {}
    diagnostics = []
    for label in sorted(results.keys()):
        result = results[label]
        text = result.full_response
        sections, diag = parse_proposal(text, agent_label=label)
        diagnostics.append(diag)

        if sections is not None:
            proposals[label] = text
        else:
            log.warning('Agent %s proposal parse failed: %s', label, diag.sections_missing)
            proposals[label] = text

        (proposals_dir / f'agent-{label}.md').write_text(text)

    write_phase_diagnostics(diagnostics, 'propose', round_dir)
    log.info('Propose: %s', summarize_phase_health(diagnostics))

    all_proposals_text = concatenate_proposals(proposals)
    (proposals_dir / 'all-proposals.md').write_text(all_proposals_text)

    return proposals


def run_debate(
    frame: Frame,
    proposals: dict[str, str],
    identity_texts: dict[str, str],
    codex_text: str,
    frame_text: str,
    prior_context: str | None,
    round_dir: Path,
    log_dir: Path,
    *,
    backend: AgentBackend | None = None,
) -> dict[str, str]:
    """Build per-agent DEBATE prompts, launch in parallel, extract debate entries.

    Returns debate_entries dict keyed by uppercase label.
    Writes agent-{label}.md and all-debate.md to round_dir/debate/.
    """
    label_map = _assign_labels(list(identity_texts.keys()))
    all_proposals_text = concatenate_proposals(proposals)
    prompts: dict[str, str] = {}
    for identity, label in label_map.items():
        prompts[label] = build_debate_prompt(
            identity_text=identity_texts[identity],
            codex_text=codex_text,
            frame_text=frame_text,
            all_proposals_text=all_proposals_text,
            prior_context=prior_context,
        )

    results = launch_parallel_agents(
        prompts,
        backend=backend,
        max_turns=DEBATE_MAX_TURNS,
        timeout=DEBATE_TIMEOUT,
        log_dir=log_dir / 'debate',
    )
    results = _enforce_quorum(
        results,
        prompts,
        backend=backend,
        max_turns=DEBATE_MAX_TURNS,
        timeout=DEBATE_TIMEOUT,
        log_dir=log_dir / 'debate',
    )

    debate_dir = round_dir / 'debate'
    debate_dir.mkdir(parents=True, exist_ok=True)

    debate_entries: dict[str, str] = {}
    for label in sorted(results.keys()):
        result = results[label]
        debate_entries[label] = result.full_response
        (debate_dir / f'agent-{label}.md').write_text(result.full_response)

    all_debate_text = concatenate_debate(debate_entries)
    (debate_dir / 'all-debate.md').write_text(all_debate_text)

    return debate_entries


def run_vote(
    frame: Frame,
    proposals: dict[str, str],
    debate_entries: dict[str, str],
    identity_texts: dict[str, str],
    codex_text: str,
    round_dir: Path,
    log_dir: Path,
    *,
    backend: AgentBackend | None = None,
) -> dict[str, VoteResult]:
    """Build per-agent VOTE prompts, launch in parallel, parse votes.

    Returns votes dict keyed by voter label.
    Writes agent-{label}.md to round_dir/votes/.
    """
    label_map = _assign_labels(list(identity_texts.keys()))
    all_proposals_text = concatenate_proposals(proposals)
    all_debate_text = concatenate_debate(debate_entries)
    valid_proposals = list(proposals.keys())

    prompts: dict[str, str] = {}
    for identity, label in label_map.items():
        prompts[label] = build_vote_prompt(
            identity_text=identity_texts[identity],
            codex_text=codex_text,
            all_proposals_text=all_proposals_text,
            all_debate_text=all_debate_text,
        )

    results = launch_parallel_agents(
        prompts,
        backend=backend,
        max_turns=VOTE_MAX_TURNS,
        timeout=VOTE_TIMEOUT,
        log_dir=log_dir / 'vote',
    )
    results = _enforce_quorum(
        results,
        prompts,
        backend=backend,
        max_turns=VOTE_MAX_TURNS,
        timeout=VOTE_TIMEOUT,
        log_dir=log_dir / 'vote',
    )

    votes_dir = round_dir / 'votes'
    votes_dir.mkdir(parents=True, exist_ok=True)

    votes: dict[str, VoteResult] = {}
    diagnostics = []
    for label in sorted(results.keys()):
        result = results[label]
        text = result.full_response
        vote, diag = parse_vote(text, agent_label=label, valid_proposals=valid_proposals)
        diagnostics.append(diag)

        if vote is not None:
            votes[label] = vote
        else:
            log.warning('Agent %s vote parse failed: %s', label, diag.sections_missing)

        (votes_dir / f'agent-{label}.md').write_text(text)

    write_phase_diagnostics(diagnostics, 'vote', round_dir)
    log.info('Vote: %s', summarize_phase_health(diagnostics))

    return votes


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------


def run_multi_agent(
    question: str,
    *,
    identities: list[str] | None = None,
    num_agents: int = 5,
    max_rounds: int = 3,
    working_dir: Path | None = None,
    backend: AgentBackend | None = None,
    codex_text: str | None = None,
    identity_texts: dict[str, str] | None = None,
) -> DecisionRecord:
    """Main entrypoint: FRAME -> cyclic PROPOSE/DEBATE/VOTE -> DECIDE.

    Parameters
    ----------
    question:
        The question to deliberate on.
    identities:
        Explicit identity filenames. If None, randomly sampled.
    num_agents:
        Number of agents (default 5).
    max_rounds:
        Maximum deliberation rounds before escalation (default 3).
    working_dir:
        Base directory for run artifacts. Defaults to ./run_ralph/multi-agent/.
    backend:
        Agent backend. Defaults to get_backend().
    codex_text:
        Pre-loaded codex text. If None, loaded from disk.
    identity_texts:
        Pre-loaded identity texts keyed by identity name. If None, loaded from disk.
    """
    run_id = _run_id()
    if working_dir is None:
        working_dir = Path('run_ralph') / 'multi-agent'
    work = working_dir / run_id
    log_dir = work / 'logs'
    work.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Select identities
    identity_names = _select_identities(identities, num_agents)

    # Build frame
    frame = Frame(
        question=question,
        scope_in=[],
        scope_out=[],
        success_criteria=['Answer the question effectively'],
        key_files=['(see question)'],
        constraints=[],
        identities=identity_names,
    )
    validate_frame(frame)

    # Load texts
    if codex_text is None:
        codex_text = load_codex()
    if identity_texts is None:
        identity_texts = {name: load_identity(name) for name in identity_names}

    frame_text = _format_frame_text(frame)

    # Write metadata
    metadata = {
        'run_id': run_id,
        'question': question,
        'num_agents': num_agents,
        'max_rounds': max_rounds,
        'identities': identity_names,
    }
    (work / 'metadata.json').write_text(json.dumps(metadata, indent=2))
    (work / 'framing.md').write_text(frame_text)

    # Deliberation loop
    prior_context: str | None = None
    tally: Tally | None = None
    proposals: dict[str, str] = {}
    debate_entries: dict[str, str] = {}
    votes: dict[str, VoteResult] = {}

    for round_num in range(1, max_rounds + 1):
        log.info('=== Round %d/%d ===', round_num, max_rounds)
        round_dir = work / f'round-{round_num}'
        round_dir.mkdir(parents=True, exist_ok=True)

        # PROPOSE
        proposals = run_propose(
            frame,
            identity_texts,
            codex_text,
            frame_text,
            prior_context,
            round_dir,
            log_dir,
            backend=backend,
        )

        # DEBATE
        debate_entries = run_debate(
            frame,
            proposals,
            identity_texts,
            codex_text,
            frame_text,
            prior_context,
            round_dir,
            log_dir,
            backend=backend,
        )

        # VOTE
        votes = run_vote(
            frame,
            proposals,
            debate_entries,
            identity_texts,
            codex_text,
            round_dir,
            log_dir,
            backend=backend,
        )

        # TALLY
        tally = compute_tally(votes, proposals)
        tally_text = (
            f'## Round {round_num} Tally\n\n'
            f'Winner: Proposal {tally.winner} ({tally.winner_pct:.0f}%)\n'
            f'Consensus: {tally.consensus_type}\n'
            f'Vetoed: {tally.vetoed}\n'
            f'Votes: {tally.vote_counts}\n'
        )
        (round_dir / 'tally.md').write_text(tally_text)
        log.info(
            'Round %d tally: %s (%s, %.0f%%)',
            round_num,
            tally.winner,
            tally.consensus_type,
            tally.winner_pct,
        )

        if tally.has_consensus():
            log.info('Consensus reached in round %d', round_num)
            break

        # Convergence detection
        if tally.consensus_type == 'split':
            top_two = sorted(tally.vote_counts, key=lambda k: tally.vote_counts[k], reverse=True)[:2]
            constraint = f'Choose ONLY between proposals {" and ".join(top_two)}. All other proposals are eliminated.'
            frame.constraints.append(constraint)
            log.info('Split detected, adding binary choice constraint: %s', constraint)

        if tally.vetoed:
            for flaws in tally.veto_flaws.values():
                for flaw in flaws:
                    frame.constraints.append(f'VETOED flaw (must address): {flaw}')
            log.info('Veto detected, adding flaw constraints')

        # Build iteration context for next round
        prior_context = build_iteration_context(
            round_num,
            proposals,
            debate_entries,
            votes,
            tally,
        )

    # DECIDE
    assert tally is not None, 'Loop must execute at least once'
    decision = build_decision(frame, tally, proposals, debate_entries, work)

    # Escalation: max_rounds exhausted without consensus
    if not tally.has_consensus():
        decision.consensus_type = 'escalated'
        decision.decision_text = (
            f'Proposal {tally.winner} selected after {max_rounds} rounds '
            f'without consensus (escalated, {tally.winner_pct:.0f}% support).'
        )
        log.warning('Max rounds reached, escalating with best available')

    (work / 'decision.md').write_text(decision.decision_text)
    log.info('Decision: %s', decision.decision_text)

    return decision
