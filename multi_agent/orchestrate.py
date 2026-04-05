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
from collections.abc import Callable
from dataclasses import dataclass
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
from multi_agent.extract import ExtractionResult, extract
from multi_agent.parallel import launch_parallel_agents
from multi_agent.parsing import (
    ParseDiagnostic,
    VoteOutput,
    VoteResult,
    _parse_concerns,
    _parse_list,
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


@dataclass
class PhaseConfig:
    """Configurable timeouts, turn limits, and prompt overrides per phase."""

    propose_timeout: int = PROPOSE_TIMEOUT
    propose_max_turns: int = PROPOSE_MAX_TURNS
    debate_timeout: int = DEBATE_TIMEOUT
    debate_max_turns: int = DEBATE_MAX_TURNS
    vote_timeout: int = VOTE_TIMEOUT
    vote_max_turns: int = VOTE_MAX_TURNS
    quorum_min: int = QUORUM_MIN
    # Prompt overrides (None = use default codex-based prompts)
    propose_task: str | None = None
    debate_task: str | None = None
    vote_task: str | None = None
    # Custom proposal sections for parsing (None = use default)
    proposal_sections: list[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extraction_to_diagnostic(
    extraction: ExtractionResult,
    agent_label: str,
    phase: str,
) -> ParseDiagnostic:
    """Convert an ExtractionResult into a ParseDiagnostic for phase health reporting."""
    last = extraction.attempts[-1] if extraction.attempts else None
    return ParseDiagnostic(
        agent_label=agent_label,
        phase=phase,
        sections_found=[],
        sections_missing=last.errors if last and not last.succeeded else [],
        headings_seen=[],
        raw_text=last.raw_text if last else '',
        parse_succeeded=extraction.succeeded,
    )


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


def _update_metadata(meta_path: Path, **updates: object) -> None:
    """Read-modify-write metadata.json with *updates*."""
    data = json.loads(meta_path.read_text())
    data.update(updates)
    meta_path.write_text(json.dumps(data, indent=2))


def _apply_convergence_constraints(frame: Frame, tally: Tally) -> None:
    """Append split/veto constraints to *frame* based on *tally* results."""
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


# ---------------------------------------------------------------------------
# Resume helpers
# ---------------------------------------------------------------------------

_PHASES = ('propose', 'debate', 'vote', 'tally')


def _phase_done(resume_phase: str, phase: str) -> bool:
    """Return True if *phase* completed before *resume_phase* began."""
    return _PHASES.index(phase) < _PHASES.index(resume_phase)


def _load_agent_files(directory: Path) -> dict[str, str]:
    """Load agent-{label}.md files from *directory*, returning {label: text}."""
    entries: dict[str, str] = {}
    if not directory.exists():
        return entries
    for f in sorted(directory.iterdir()):
        if f.name.startswith('agent-') and f.suffix == '.md':
            label = f.stem.split('-', 1)[1]
            entries[label] = f.read_text()
    return entries


def _load_round_proposals(round_dir: Path) -> dict[str, str]:
    return _load_agent_files(round_dir / 'proposals')


def _load_round_debate(round_dir: Path) -> dict[str, str]:
    return _load_agent_files(round_dir / 'debate')


def _load_round_votes(round_dir: Path, valid_proposals: list[str]) -> dict[str, VoteResult]:
    texts = _load_agent_files(round_dir / 'votes')
    votes: dict[str, VoteResult] = {}
    for label, text in texts.items():
        vote, _ = parse_vote(text, agent_label=label, valid_proposals=valid_proposals)
        if vote is not None:
            votes[label] = vote
    return votes


# ---------------------------------------------------------------------------
# Quorum enforcement
# ---------------------------------------------------------------------------


def _agent_succeeded(result: AgentResult) -> bool:
    """Return True if the agent produced a usable response."""
    return result.exit_code == 0 and not result.timed_out and bool(result.full_response.strip())


def _enforce_quorum(
    results: dict[str, AgentResult],
    prompts: dict[str, str],
    *,
    backend: AgentBackend | None,
    max_turns: int,
    timeout: int,
    log_dir: Path,
    log_prefix: str = '',
    quorum_min: int = QUORUM_MIN,
) -> dict[str, AgentResult]:
    """Retry failed agents once. Raise RuntimeError if still below *quorum_min*."""
    failed = {label: prompts[label] for label, r in results.items() if not _agent_succeeded(r)}

    if not failed:
        return results

    succeeded = sum(1 for r in results.values() if _agent_succeeded(r))
    if succeeded >= quorum_min:
        log.info(
            'Quorum already met (%d/%d succeeded), skipping retry of %d failed agents: %s',
            succeeded,
            len(results),
            len(failed),
            list(failed.keys()),
        )
        return results

    log.info('Retrying %d failed agents: %s', len(failed), list(failed.keys()))
    retry_results = launch_parallel_agents(
        failed,
        backend=backend,
        max_turns=max_turns,
        timeout=timeout,
        log_dir=log_dir,
        log_prefix=f'{log_prefix}retry-',
    )

    merged = dict(results)
    for label, result in retry_results.items():
        if _agent_succeeded(result):
            merged[label] = result

    succeeded = sum(1 for r in merged.values() if _agent_succeeded(r))
    if succeeded < quorum_min:
        msg = f'Quorum not met: {succeeded}/{len(merged)} agents succeeded (need {quorum_min})'
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
    log_prefix: str = '',
    phase_config: PhaseConfig | None = None,
) -> dict[str, str]:
    """Build per-agent PROPOSE prompts, launch in parallel, parse proposals.

    Returns proposals dict keyed by uppercase label (A, B, C, ...).
    Writes agent-{label}.md and all-proposals.md to round_dir/proposals/.
    """
    cfg = phase_config or PhaseConfig()
    label_map = _assign_labels(list(identity_texts.keys()))
    prompts: dict[str, str] = {}
    for identity, label in label_map.items():
        prompts[label] = build_propose_prompt(
            identity_text=identity_texts[identity],
            codex_text=codex_text,
            frame_text=frame_text,
            prior_context=prior_context,
            task_instructions=cfg.propose_task,
        )

    phase_prefix = f'{log_prefix}propose-'
    results = launch_parallel_agents(
        prompts,
        backend=backend,
        max_turns=cfg.propose_max_turns,
        timeout=cfg.propose_timeout,
        log_dir=log_dir,
        log_prefix=phase_prefix,
    )
    results = _enforce_quorum(
        results,
        prompts,
        backend=backend,
        max_turns=cfg.propose_max_turns,
        timeout=cfg.propose_timeout,
        log_dir=log_dir,
        log_prefix=phase_prefix,
        quorum_min=cfg.quorum_min,
    )

    proposals_dir = round_dir / 'proposals'
    proposals_dir.mkdir(parents=True, exist_ok=True)

    proposals: dict[str, str] = {}
    diagnostics = []
    for label in sorted(results.keys()):
        result = results[label]
        text = result.full_response
        sections, diag = parse_proposal(text, agent_label=label, required_sections=cfg.proposal_sections)
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
    log_prefix: str = '',
    phase_config: PhaseConfig | None = None,
) -> dict[str, str]:
    """Build per-agent DEBATE prompts, launch in parallel, extract debate entries.

    Returns debate_entries dict keyed by uppercase label.
    Writes agent-{label}.md and all-debate.md to round_dir/debate/.
    """
    cfg = phase_config or PhaseConfig()
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
            task_instructions=cfg.debate_task,
        )

    phase_prefix = f'{log_prefix}debate-'
    results = launch_parallel_agents(
        prompts,
        backend=backend,
        max_turns=cfg.debate_max_turns,
        timeout=cfg.debate_timeout,
        log_dir=log_dir,
        log_prefix=phase_prefix,
    )
    results = _enforce_quorum(
        results,
        prompts,
        backend=backend,
        max_turns=cfg.debate_max_turns,
        timeout=cfg.debate_timeout,
        log_dir=log_dir,
        quorum_min=cfg.quorum_min,
        log_prefix=phase_prefix,
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
    log_prefix: str = '',
    phase_config: PhaseConfig | None = None,
) -> dict[str, VoteResult]:
    """Build per-agent VOTE prompts, launch in parallel, parse votes.

    Returns votes dict keyed by voter label.
    Writes agent-{label}.md to round_dir/votes/.
    """
    cfg = phase_config or PhaseConfig()
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
            task_instructions=cfg.vote_task,
        )

    phase_prefix = f'{log_prefix}vote-'
    results = launch_parallel_agents(
        prompts,
        backend=backend,
        max_turns=cfg.vote_max_turns,
        timeout=cfg.vote_timeout,
        log_dir=log_dir,
        log_prefix=phase_prefix,
    )
    results = _enforce_quorum(
        results,
        prompts,
        backend=backend,
        max_turns=cfg.vote_max_turns,
        timeout=cfg.vote_timeout,
        log_dir=log_dir,
        log_prefix=phase_prefix,
        quorum_min=cfg.quorum_min,
    )

    votes_dir = round_dir / 'votes'
    votes_dir.mkdir(parents=True, exist_ok=True)

    def _make_invoke(label: str) -> Callable[[str], AgentResult]:
        """Closure that re-runs a single agent for extract retry."""

        def _invoke(correction_prompt: str) -> AgentResult:
            retry = launch_parallel_agents(
                {label: correction_prompt},
                backend=backend,
                max_turns=cfg.vote_max_turns,
                timeout=cfg.vote_timeout,
                log_dir=log_dir,
                log_prefix=f'{phase_prefix}retry-{label}-',
            )
            return retry[label]

        return _invoke

    votes: dict[str, VoteResult] = {}
    diagnostics = []
    for label in sorted(results.keys()):
        result = results[label]
        text = result.full_response

        extraction = extract(
            text,
            VoteOutput,
            prompt=prompts[label],
            invoke=_make_invoke(label),
            max_attempts=2,
        )

        if extraction.succeeded:
            vo = extraction.value
            assert vo is not None  # guaranteed by succeeded
            if valid_proposals and vo.winner not in valid_proposals:
                log.warning('Agent %s voted for unknown proposal %s', label, vo.winner)
            else:
                votes[label] = VoteResult(
                    voter_label=label,
                    winner=vo.winner,
                    decisive_argument=vo.decisive_argument,
                    concerns=_parse_concerns(vo.concerns_about_the_winner),
                    unrefuted_arguments=_parse_list(vo.unrefuted_arguments),
                    merge_suggestion=vo.merge_suggestion or None,
                )
        else:
            last = extraction.attempts[-1] if extraction.attempts else None
            errors = last.errors if last else ['no attempts']
            log.warning('Agent %s vote extraction failed: %s', label, errors)

        # Write final text (possibly from retry attempt)
        final_text = extraction.attempts[-1].raw_text if extraction.attempts else text
        (votes_dir / f'agent-{label}.md').write_text(final_text)

        # Build diagnostic for phase health reporting
        diag = _extraction_to_diagnostic(extraction, label, 'vote')
        diagnostics.append(diag)

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
    resume_run_id: str | None = None,
    phase_config: PhaseConfig | None = None,
    skip_frame_validation: bool = False,
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
    resume_run_id:
        If set, resume a partial run by its run_id instead of starting fresh.
    phase_config:
        Phase timeouts, turn limits, and prompt overrides. Defaults to standard config.
    skip_frame_validation:
        If True, skip the ``validate_frame()`` check (useful for fast mode).
    """
    if working_dir is None:
        working_dir = Path('run_ralph') / 'multi-agent'

    # ---- Resume vs fresh setup ----
    start_round = 1
    resume_phase: str | None = None

    if resume_run_id is not None:
        work = working_dir / resume_run_id
        meta_path = work / 'metadata.json'
        prev_meta = json.loads(meta_path.read_text())
        run_id = prev_meta['run_id']
        question = prev_meta['question']
        num_agents = prev_meta['num_agents']
        max_rounds = prev_meta['max_rounds']
        identity_names: list[str] = prev_meta['identities']
        start_round = max(prev_meta.get('current_round', 1), 1)
        resume_phase = prev_meta.get('current_phase', 'propose')
        _update_metadata(meta_path, status='in_progress', finished_at=None)
    else:
        run_id = _run_id()
        work = working_dir / run_id
        identity_names = _select_identities(identities, num_agents)

    log_dir = work / 'logs'
    work.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

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
    if not skip_frame_validation:
        validate_frame(frame)

    # Load texts
    if codex_text is None:
        codex_text = load_codex()
    if identity_texts is None:
        identity_texts = {name: load_identity(name) for name in identity_names}

    frame_text = _format_frame_text(frame)

    # Write metadata (fresh run only; resume already has metadata)
    meta_path = work / 'metadata.json'
    if resume_run_id is None:
        metadata = {
            'run_id': run_id,
            'question': question,
            'num_agents': num_agents,
            'max_rounds': max_rounds,
            'identities': identity_names,
            'started_at': datetime.now(tz=timezone.utc).isoformat(),
            'finished_at': None,
            'status': 'in_progress',
            'current_round': 0,
            'current_phase': 'framing',
        }
        meta_path.write_text(json.dumps(metadata, indent=2))
    (work / 'framing.md').write_text(frame_text)

    # ---- Replay completed rounds on resume ----
    prior_context: str | None = None
    tally: Tally | None = None
    proposals: dict[str, str] = {}
    debate_entries: dict[str, str] = {}
    votes: dict[str, VoteResult] = {}

    if resume_run_id is not None:
        for rn in range(1, start_round):
            rd = work / f'round-{rn}'
            proposals = _load_round_proposals(rd)
            debate_entries = _load_round_debate(rd)
            votes = _load_round_votes(rd, list(proposals.keys()))
            tally = compute_tally(votes, proposals)
            _apply_convergence_constraints(frame, tally)
            prior_context = build_iteration_context(
                rn,
                proposals,
                debate_entries,
                votes,
                tally,
            )
        log.info('Resumed run %s from round %d phase %s', resume_run_id, start_round, resume_phase)

    try:
        for round_num in range(start_round, max_rounds + 1):
            log.info('=== Round %d/%d ===', round_num, max_rounds)
            round_dir = work / f'round-{round_num}'
            round_dir.mkdir(parents=True, exist_ok=True)

            rnd_prefix = f'round-{round_num}-'
            resuming = round_num == start_round and resume_phase is not None

            # PROPOSE
            if resuming and _phase_done(resume_phase, 'propose'):
                proposals = _load_round_proposals(round_dir)
            else:
                _update_metadata(meta_path, current_round=round_num, current_phase='propose')
                proposals = run_propose(
                    frame,
                    identity_texts,
                    codex_text,
                    frame_text,
                    prior_context,
                    round_dir,
                    log_dir,
                    backend=backend,
                    log_prefix=rnd_prefix,
                    phase_config=phase_config,
                )

            # DEBATE
            if resuming and _phase_done(resume_phase, 'debate'):
                debate_entries = _load_round_debate(round_dir)
            else:
                _update_metadata(meta_path, current_phase='debate')
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
                    log_prefix=rnd_prefix,
                    phase_config=phase_config,
                )

            # VOTE
            if resuming and _phase_done(resume_phase, 'vote'):
                votes = _load_round_votes(round_dir, list(proposals.keys()))
            else:
                _update_metadata(meta_path, current_phase='vote')
                votes = run_vote(
                    frame,
                    proposals,
                    debate_entries,
                    identity_texts,
                    codex_text,
                    round_dir,
                    log_dir,
                    backend=backend,
                    log_prefix=rnd_prefix,
                    phase_config=phase_config,
                )

            # TALLY (always recompute)
            _update_metadata(meta_path, current_phase='tally')
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

            # Convergence constraints
            _apply_convergence_constraints(frame, tally)

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

        final_status = 'escalated' if decision.consensus_type == 'escalated' else 'completed'
        _update_metadata(
            meta_path,
            finished_at=datetime.now(tz=timezone.utc).isoformat(),
            status=final_status,
        )

        (work / 'decision.md').write_text(decision.decision_text)
        log.info('Decision: %s', decision.decision_text)

        return decision

    except BaseException:
        _update_metadata(
            meta_path,
            finished_at=datetime.now(tz=timezone.utc).isoformat(),
            status='failed',
        )
        raise
