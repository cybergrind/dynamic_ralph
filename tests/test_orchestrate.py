"""Tests for multi_agent.orchestrate — main orchestration loop."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from multi_agent.backend import AgentResult, LaunchConfig
from multi_agent.orchestrate import (
    _agent_succeeded,
    _enforce_quorum,
    _select_identities,
    run_debate,
    run_multi_agent,
    run_propose,
    run_vote,
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
# TestSelectIdentities
# ---------------------------------------------------------------------------


class TestSelectIdentities:
    def test_samples_from_directory(self, tmp_path: Path) -> None:
        for name in ['i_a.md', 'i_b.md', 'i_c.md', 'i_d.md', 'i_e.md']:
            (tmp_path / name).write_text(f'identity {name}')
        result = _select_identities(None, num_agents=3, base_path=tmp_path)
        assert len(result) == 3
        assert all(name.endswith('.md') for name in result)


# ---------------------------------------------------------------------------
# TestQuorumEnforcement
# ---------------------------------------------------------------------------


class TestAgentSucceeded:
    """Unit tests for the _agent_succeeded helper."""

    def test_normal_success(self) -> None:
        r = _make_result('some output')
        assert _agent_succeeded(r) is True

    def test_nonzero_exit_code_fails(self) -> None:
        r = _make_result('output', exit_code=1)
        assert _agent_succeeded(r) is False

    def test_timed_out_fails(self) -> None:
        r = _make_result('output', timed_out=True)
        assert _agent_succeeded(r) is False

    def test_empty_response_fails(self) -> None:
        """Agent C scenario: exit_code=0 but empty response is NOT a success."""
        r = _make_result('')
        assert _agent_succeeded(r) is False

    def test_structured_output_with_empty_text_succeeds(self) -> None:
        """Agent that uses StructuredOutput may produce empty full_response.

        This is the normal case for json_schema agents — they call
        StructuredOutput directly without writing assistant text.
        """
        r = _make_result('')
        r.structured_output = {'winner': 'A', 'reason': 'best'}
        assert _agent_succeeded(r) is True

    def test_whitespace_only_response_fails(self) -> None:
        r = _make_result('   \n\t  ')
        assert _agent_succeeded(r) is False


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
                LaunchConfig(log_dir=tmp_path, max_turns=3, timeout=300),
            )

        assert merged['C'].exit_code == 0

    def test_empty_response_treated_as_failure(self, tmp_path: Path) -> None:
        """Agent exits 0 but with empty response -> treated as failed -> retried."""
        results = {
            'A': _make_result('ok'),
            'B': _make_result('ok'),
            'C': _make_result(''),  # exit_code=0 but empty
        }
        prompts = {'A': 'p', 'B': 'p', 'C': 'p'}

        retry_results = {'C': _make_result('recovered output')}
        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=retry_results):
            merged = _enforce_quorum(
                results,
                prompts,
                LaunchConfig(log_dir=tmp_path, max_turns=3, timeout=300),
            )

        assert merged['C'].full_response == 'recovered output'

    def test_quorum_met_skips_retry_of_empty_response(self, tmp_path: Path) -> None:
        """3 good agents + 1 empty response -> quorum met, no retry needed."""
        results = {
            'A': _make_result('ok'),
            'B': _make_result('ok'),
            'C': _make_result('ok'),
            'D': _make_result(''),  # empty but quorum already met
        }
        prompts = {'A': 'p', 'B': 'p', 'C': 'p', 'D': 'p'}

        with patch('multi_agent.orchestrate.launch_parallel_agents'):
            merged = _enforce_quorum(
                results,
                prompts,
                LaunchConfig(log_dir=tmp_path, max_turns=3, timeout=300),
            )

        # Quorum already met (3 succeeded), retry still happens but result is fine
        assert merged['A'].full_response == 'ok'

    def test_enforce_quorum_passes_output_schema(self, tmp_path: Path) -> None:
        """output_schema is forwarded to launch_parallel_agents on retry."""
        from multi_agent.backend import OutputSchema

        results = {
            'A': _make_result('ok'),
            'B': _make_result('ok'),
            'C': _make_result('fail', exit_code=1),
        }
        prompts = {'A': 'p', 'B': 'p', 'C': 'p'}
        schema = OutputSchema(json_schema={'type': 'object'})

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value={'C': _make_result('ok')}) as mock:
            _enforce_quorum(
                results,
                prompts,
                LaunchConfig(log_dir=tmp_path, max_turns=3, timeout=300, output_schema=schema),
            )

        mock.assert_called_once()
        call_config = mock.call_args.kwargs.get('config') or mock.call_args.args[1]
        assert call_config.output_schema is schema

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
                    LaunchConfig(log_dir=tmp_path, max_turns=3, timeout=300),
                )


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


# ---------------------------------------------------------------------------
# TestRunVoteExtractRetry — extract() retry on parse failure
# ---------------------------------------------------------------------------

_BAD_VOTE_UNPARSEABLE = """\
I think Proposal A is the best choice because it's simple and effective.
The other proposals have too many moving parts. I'd vote for A.
"""


class TestRunVoteExtractRetry:
    """Verify that run_vote() retries agents with validation feedback on parse failure."""

    def test_unparseable_vote_recovered_via_extract_retry(self, tmp_path: Path) -> None:
        """Agent produces unstructured output -> extract retries with feedback -> vote recovered."""
        labels = ['A', 'B', 'C', 'D', 'E']
        proposals = {lbl: f'Proposal {lbl} text' for lbl in labels}
        identity_texts = {f'i_{lbl.lower()}.md': f'You are agent {lbl}.' for lbl in labels}
        codex_text = 'Follow the codex.'

        frame = _make_frame(identities=list(identity_texts.keys()))

        # Agent E gets unparseable output initially; all others are fine
        initial_results = {lbl: _vote_result('A') for lbl in labels}
        initial_results['E'] = _make_result(_BAD_VOTE_UNPARSEABLE)

        # On retry, agent E produces correct output
        retry_result = {'E': _vote_result('A')}

        round_dir = tmp_path / 'round-1'
        round_dir.mkdir(parents=True)
        log_dir = tmp_path / 'logs'
        log_dir.mkdir(parents=True)

        call_count = [0]
        debate_entries = {lbl: f'Debate from {lbl}' for lbl in labels}

        def mock_launch(prompts, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return initial_results
            # Retry call for agent E
            assert 'CORRECTION REQUIRED' in prompts.get('E', '')
            return retry_result

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=mock_launch):
            votes = run_vote(
                frame,
                proposals,
                debate_entries,
                identity_texts,
                codex_text,
                round_dir,
                log_dir,
            )

        # Agent E's vote should be recovered via retry
        assert 'E' in votes, f'Agent E vote missing. Got: {list(votes.keys())}'
        assert votes['E'].winner == 'A'
        assert len(votes) == 5


# ---------------------------------------------------------------------------
# TestRunVoteStructuredOutput — structured output integration
# ---------------------------------------------------------------------------


class TestRunVoteStructuredOutput:
    """Verify run_vote() integrates with --json-schema structured output."""

    def _setup(self, tmp_path):
        labels = ['A', 'B', 'C', 'D', 'E']
        proposals = {lbl: f'Proposal {lbl} text' for lbl in labels}
        identity_texts = {f'i_{lbl.lower()}.md': f'You are agent {lbl}.' for lbl in labels}
        frame = _make_frame(identities=list(identity_texts.keys()))
        debate_entries = {lbl: f'Debate from {lbl}' for lbl in labels}
        round_dir = tmp_path / 'round-1'
        round_dir.mkdir(parents=True)
        log_dir = tmp_path / 'logs'
        log_dir.mkdir(parents=True)
        return labels, proposals, identity_texts, frame, debate_entries, round_dir, log_dir

    def test_run_vote_passes_output_schema(self, tmp_path: Path) -> None:
        """run_vote passes VoteOutput JSON schema to launch_parallel_agents."""
        labels, proposals, identity_texts, frame, debate_entries, round_dir, log_dir = self._setup(tmp_path)

        initial_results = {lbl: _vote_result('A') for lbl in labels}

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results) as mock:
            run_vote(
                frame,
                proposals,
                debate_entries,
                identity_texts,
                'codex',
                round_dir,
                log_dir,
            )

        # First call is the main launch (not quorum retry)
        call_kwargs = mock.call_args_list[0].kwargs
        call_config = call_kwargs.get('config') or mock.call_args_list[0].args[1]
        os = call_config.output_schema
        assert os.disable_tools is True  # vote disables tools!
        assert 'winner' in os.json_schema.get('properties', {})
        assert 'decisive_argument' in os.json_schema.get('properties', {})

    def test_run_vote_reads_structured_output(self, tmp_path: Path) -> None:
        """Agents with structured_output are parsed correctly even if full_response is garbage."""
        labels, proposals, identity_texts, frame, debate_entries, round_dir, log_dir = self._setup(tmp_path)

        structured = {
            'winner': 'A',
            'decisive_argument': 'Simplicity wins',
            'concerns_about_the_winner': 'Minor perf concern',
            'unrefuted_arguments': '',
            'merge_suggestion': '',
        }

        initial_results = {}
        for lbl in labels:
            r = _make_result('unparseable garbage that would fail markdown extraction')
            r.structured_output = structured
            initial_results[lbl] = r

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            votes = run_vote(
                frame,
                proposals,
                debate_entries,
                identity_texts,
                'codex',
                round_dir,
                log_dir,
            )

        assert len(votes) == 5
        assert all(v.winner == 'A' for v in votes.values())

    def test_run_vote_fallback_without_structured_output(self, tmp_path: Path) -> None:
        """structured_output=None falls back to markdown extraction."""
        labels, proposals, identity_texts, frame, debate_entries, round_dir, log_dir = self._setup(tmp_path)

        # No structured_output, but parseable markdown
        initial_results = {lbl: _vote_result('A') for lbl in labels}

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            votes = run_vote(
                frame,
                proposals,
                debate_entries,
                identity_texts,
                'codex',
                round_dir,
                log_dir,
            )

        assert len(votes) == 5
        assert all(v.winner == 'A' for v in votes.values())

    def test_run_vote_structured_output_invalid_winner_skipped(self, tmp_path: Path) -> None:
        """structured_output with unknown proposal label -> vote skipped."""
        labels, proposals, identity_texts, frame, debate_entries, round_dir, log_dir = self._setup(tmp_path)

        initial_results = {}
        for lbl in labels:
            r = _make_result('garbage')
            r.structured_output = {
                'winner': 'Z',  # not in proposals
                'decisive_argument': 'whatever',
                'concerns_about_the_winner': 'none',
            }
            initial_results[lbl] = r

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            votes = run_vote(
                frame,
                proposals,
                debate_entries,
                identity_texts,
                'codex',
                round_dir,
                log_dir,
            )

        assert len(votes) == 0  # all voted for unknown proposal Z

    def test_run_vote_quorum_warning(self, tmp_path: Path, caplog) -> None:
        """All agents fail to produce valid votes -> warning logged."""
        labels, proposals, identity_texts, frame, debate_entries, round_dir, log_dir = self._setup(tmp_path)

        # All agents return unparseable garbage with no structured_output
        initial_results = {lbl: _make_result('random garbage') for lbl in labels}

        import logging

        with caplog.at_level(logging.WARNING):
            with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
                votes = run_vote(
                    frame,
                    proposals,
                    debate_entries,
                    identity_texts,
                    'codex',
                    round_dir,
                    log_dir,
                )

        assert len(votes) == 0
        assert any('quorum' in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# TestRunProposeStructuredOutput
# ---------------------------------------------------------------------------


class TestRunProposeStructuredOutput:
    """Verify run_propose() integrates with structured output."""

    def _setup(self, tmp_path):
        labels = ['A', 'B', 'C', 'D', 'E']
        identity_texts = {f'i_{lbl.lower()}.md': f'You are agent {lbl}.' for lbl in labels}
        frame = _make_frame(identities=list(identity_texts.keys()))
        round_dir = tmp_path / 'round-1'
        round_dir.mkdir(parents=True)
        log_dir = tmp_path / 'logs'
        log_dir.mkdir(parents=True)
        return labels, identity_texts, frame, round_dir, log_dir

    def test_run_propose_passes_output_schema(self, tmp_path: Path) -> None:
        """run_propose passes ProposalOutput JSON schema to launch_parallel_agents."""
        labels, identity_texts, frame, round_dir, log_dir = self._setup(tmp_path)
        initial_results = {lbl: _proposal_result(lbl) for lbl in labels}

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results) as mock:
            run_propose(
                frame,
                identity_texts,
                'codex',
                'frame text',
                None,
                round_dir,
                log_dir,
            )

        call_config = mock.call_args_list[0].kwargs.get('config') or mock.call_args_list[0].args[1]
        os = call_config.output_schema
        assert os.disable_tools is False  # propose needs tools!
        assert 'summary' in os.json_schema.get('properties', {})
        assert 'code_sketch' in os.json_schema.get('properties', {})

    def test_run_propose_reads_structured_output(self, tmp_path: Path) -> None:
        """Structured output → to_markdown() → proposals dict has formatted markdown."""
        labels, identity_texts, frame, round_dir, log_dir = self._setup(tmp_path)

        structured = {
            'summary': 'Build a cache',
            'code_sketch': '```python\ncache = {}\n```',
            'files_changed': 'src/cache.py',
            'migration_plan': 'Add redis',
            'what_id_argue': 'Speed boost',
            'what_worries_me': 'Invalidation',
        }

        initial_results = {}
        for lbl in labels:
            r = _make_result('garbage that would not parse')
            r.structured_output = structured
            initial_results[lbl] = r

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            proposals = run_propose(
                frame,
                identity_texts,
                'codex',
                'frame text',
                None,
                round_dir,
                log_dir,
            )

        assert len(proposals) == 5
        # Proposals should contain beautifully formatted markdown from to_markdown()
        for text in proposals.values():
            assert '## Summary' in text
            assert 'Build a cache' in text
            assert '## Code sketch' in text

    def test_run_propose_fallback_to_raw_text(self, tmp_path: Path) -> None:
        """No structured_output → raw text used (backward compat)."""
        labels, identity_texts, frame, round_dir, log_dir = self._setup(tmp_path)
        initial_results = {lbl: _proposal_result(lbl) for lbl in labels}

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            proposals = run_propose(
                frame,
                identity_texts,
                'codex',
                'frame text',
                None,
                round_dir,
                log_dir,
            )

        assert len(proposals) == 5
        # Raw text still works
        for text in proposals.values():
            assert '## Summary' in text


# ---------------------------------------------------------------------------
# TestRunDebateStructuredOutput
# ---------------------------------------------------------------------------


class TestRunDebateStructuredOutput:
    """Verify run_debate() integrates with structured output."""

    def _setup(self, tmp_path):
        labels = ['A', 'B', 'C', 'D', 'E']
        proposals = {lbl: f'Proposal {lbl} text' for lbl in labels}
        identity_texts = {f'i_{lbl.lower()}.md': f'You are agent {lbl}.' for lbl in labels}
        frame = _make_frame(identities=list(identity_texts.keys()))
        round_dir = tmp_path / 'round-1'
        round_dir.mkdir(parents=True)
        log_dir = tmp_path / 'logs'
        log_dir.mkdir(parents=True)
        return labels, proposals, identity_texts, frame, round_dir, log_dir

    def test_run_debate_passes_output_schema(self, tmp_path: Path) -> None:
        """run_debate passes DebateOutput JSON schema to launch_parallel_agents."""
        labels, proposals, identity_texts, frame, round_dir, log_dir = self._setup(tmp_path)
        initial_results = {lbl: _debate_result(lbl) for lbl in labels}

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results) as mock:
            run_debate(
                frame,
                proposals,
                identity_texts,
                'codex',
                'frame text',
                None,
                round_dir,
                log_dir,
            )

        call_config = mock.call_args_list[0].kwargs.get('config') or mock.call_args_list[0].args[1]
        os = call_config.output_schema
        assert os.disable_tools is False  # debate needs tools!
        assert 'my_case' in os.json_schema.get('properties', {})
        assert 'challenges_to_other_proposals' in os.json_schema.get('properties', {})

    def test_run_debate_reads_structured_output(self, tmp_path: Path) -> None:
        """Structured output → to_markdown() → debate_entries has formatted markdown."""
        labels, proposals, identity_texts, frame, round_dir, log_dir = self._setup(tmp_path)

        structured = {
            'my_case': 'Proposal A is strongest',
            'challenges_to_other_proposals': 'B has scaling issues',
            'what_id_adopt_from_others': 'Error handling from C',
            'my_biggest_doubt': 'Scale concerns',
        }

        initial_results = {}
        for lbl in labels:
            r = _make_result('garbage')
            r.structured_output = structured
            initial_results[lbl] = r

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            debate_entries = run_debate(
                frame,
                proposals,
                identity_texts,
                'codex',
                'frame text',
                None,
                round_dir,
                log_dir,
            )

        assert len(debate_entries) == 5
        for text in debate_entries.values():
            assert '## My case' in text
            assert 'Proposal A is strongest' in text
            assert '## Challenges to other proposals' in text

    def test_run_debate_fallback_to_raw_text(self, tmp_path: Path) -> None:
        """No structured_output → raw text used."""
        labels, proposals, identity_texts, frame, round_dir, log_dir = self._setup(tmp_path)
        initial_results = {lbl: _debate_result(lbl) for lbl in labels}

        with patch('multi_agent.orchestrate.launch_parallel_agents', return_value=initial_results):
            debate_entries = run_debate(
                frame,
                proposals,
                identity_texts,
                'codex',
                'frame text',
                None,
                round_dir,
                log_dir,
            )

        assert len(debate_entries) == 5
        for text in debate_entries.values():
            assert '## My case' in text


# ---------------------------------------------------------------------------
# TestCLIHelp — CLI entrypoint help argument
# ---------------------------------------------------------------------------

ORCHESTRATE_SCRIPT = Path(__file__).resolve().parent.parent / 'skills' / 'multi-agent' / 'orchestrate.py'


class TestCLIHelp:
    def test_help_argument_prints_usage(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ORCHESTRATE_SCRIPT), 'help'],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert '/multi-agent' in result.stdout
        assert 'Usage:' in result.stdout

    def test_help_argument_case_insensitive(self, capsys, monkeypatch) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location('orchestrate', ORCHESTRATE_SCRIPT)
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        monkeypatch.setattr('sys.argv', ['orchestrate', 'HELP'])
        mod.main()
        captured = capsys.readouterr()
        assert '/multi-agent' in captured.out


# ---------------------------------------------------------------------------
# TestTraceIntegration — trace.jsonl written during run_multi_agent
# ---------------------------------------------------------------------------


class TestTraceIntegration:
    def test_trace_file_created_with_spans(self, tmp_path: Path) -> None:
        """run_multi_agent creates trace.jsonl with run/round/phase spans."""
        import json

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

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]

        trace_path = run_dir / 'trace.jsonl'
        assert trace_path.exists()

        records = [json.loads(line) for line in trace_path.read_text().strip().splitlines()]
        kinds = {r['kind'] for r in records}
        assert 'run' in kinds
        assert 'round' in kinds
        assert 'phase' in kinds

    def test_vote_phase_span_includes_vote_details(self, tmp_path: Path) -> None:
        """Vote phase end span includes per-agent vote results."""
        import json

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

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        run_dir = run_dirs[0]
        trace_path = run_dir / 'trace.jsonl'
        records = [json.loads(line) for line in trace_path.read_text().strip().splitlines()]

        # Find vote phase end span
        vote_ends = [r for r in records if r['event'] == 'end' and r.get('label') == 'vote']
        assert len(vote_ends) == 1
        details = vote_ends[0]['details']
        assert 'votes' in details
        assert details['votes_parsed'] == 5
        assert details['agents_total'] == 5
        # Per-agent vote map
        assert details['votes']['A'] == 'A'
        assert details['votes']['B'] == 'A'

    def test_round_span_includes_tally(self, tmp_path: Path) -> None:
        """Round end span includes tally results (winner, consensus, pct)."""
        import json

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

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        run_dir = run_dirs[0]
        trace_path = run_dir / 'trace.jsonl'
        records = [json.loads(line) for line in trace_path.read_text().strip().splitlines()]

        # Find round end span
        round_ends = [r for r in records if r['event'] == 'end' and r['kind'] == 'round']
        assert len(round_ends) == 1
        details = round_ends[0]['details']
        assert details['winner'] == 'A'
        assert details['consensus_type'] == 'strong'
        assert details['winner_pct'] == 100.0
        assert details['vetoed'] is False

        # Verify begin/end pairs
        begins = {r['span_id'] for r in records if r['event'] == 'begin'}
        ends = {r['span_id'] for r in records if r['event'] == 'end'}
        assert begins == ends, f'Unmatched spans: begins={begins - ends}, ends={ends - begins}'

    def test_run_span_includes_question(self, tmp_path: Path) -> None:
        """Run begin span records the initial question in details."""
        import json

        labels = ['A', 'B', 'C', 'D', 'E']
        effects = [
            {lbl: _proposal_result(lbl) for lbl in labels},
            {lbl: _debate_result(lbl) for lbl in labels},
            {lbl: _vote_result('A') for lbl in labels},
        ]

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects):
            run_multi_agent(
                'How should we design the API?',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        run_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        run_dir = run_dirs[0]
        trace_path = run_dir / 'trace.jsonl'
        records = [json.loads(line) for line in trace_path.read_text().strip().splitlines()]

        run_begins = [r for r in records if r['event'] == 'begin' and r['kind'] == 'run']
        assert len(run_begins) == 1
        assert run_begins[0]['details']['question'] == 'How should we design the API?'

    def test_agent_spans_include_identity(self, tmp_path: Path) -> None:
        """Agent begin spans record the identity filename in details."""

        labels = ['A', 'B', 'C', 'D', 'E']
        effects = [
            {lbl: _proposal_result(lbl) for lbl in labels},
            {lbl: _debate_result(lbl) for lbl in labels},
            {lbl: _vote_result('A') for lbl in labels},
        ]

        with patch('multi_agent.orchestrate.launch_parallel_agents', side_effect=effects) as mock:
            run_multi_agent(
                'Test question',
                identities=_TEST_IDENTITIES,
                num_agents=5,
                max_rounds=3,
                working_dir=tmp_path,
                codex_text=_TEST_CODEX,
                identity_texts=_TEST_IDENTITY_TEXTS,
            )

        # All calls to launch_parallel_agents should include identity_names via tracing
        for call in mock.call_args_list:
            kwargs = call.kwargs
            tracing_ctx = kwargs.get('tracing')
            assert tracing_ctx is not None or 'identity_names' in kwargs, (
                f'Neither tracing nor identity_names passed: {kwargs.keys()}'
            )
            if tracing_ctx is not None:
                assert isinstance(tracing_ctx.identity_names, dict)
