"""CLI entrypoint for the fast multi-agent orchestrator.

Lightweight variant that skips code reading. Uses inline identities,
a minimal codex, and tight timeouts to complete in under 1 minute.
"""

import argparse
import logging
from pathlib import Path

from multi_agent.orchestrate import PhaseConfig, run_multi_agent


_FAST_CODEX = (Path(__file__).resolve().parent / 'fast_codex.md').read_text()

# Short inline identities -- enough for differentiation, no deep analysis triggers.
_IDENTITY_SKETCHES: dict[str, str] = {
    'pragmatist': (
        '# Identity: The Pragmatist\n\n'
        'You value simplicity and shipping. You prefer the approach that works '
        'today over the one that might be elegant tomorrow. You distrust '
        "abstractions that don't pay for themselves immediately. Your motto: "
        '"Does it work? Ship it."'
    ),
    'architect': (
        '# Identity: The Architect\n\n'
        'You think about long-term maintainability and systemic consequences. '
        'You look for patterns that scale and resist entropy. You worry about '
        'decisions that are hard to reverse. Your motto: "What does this look '
        'like in two years?"'
    ),
    'skeptic': (
        '# Identity: The Skeptic\n\n'
        'You stress-test every proposal. You look for hidden assumptions, edge '
        'cases, and failure modes. You trust concrete evidence over theoretical '
        'arguments. Your motto: "What could go wrong?"'
    ),
}

_FAST_PROPOSE_TASK = (
    '## Your Task\n\n'
    'The Question section below is your PRIMARY DIRECTIVE. '
    'Read it carefully and follow any specific instructions it contains.\n\n'
    'Write your proposal following the codex format. '
    'Include: Summary, Approach, Strengths, Weaknesses.\n\n'
    'The Question takes priority over format requirements — '
    'if the Question asks for something specific, do exactly that.\n\n'
    'IMPORTANT: Do NOT use any tools. Do NOT read files. '
    'Respond immediately with your proposal text. Keep it under 500 words.'
)

_FAST_DEBATE_TASK = (
    '## Your Task\n\n'
    'The Question section below is your PRIMARY DIRECTIVE. '
    'Read it carefully and follow any specific instructions it contains.\n\n'
    'Write your debate entry following the codex format. '
    'Include: My case, Challenges to other proposals, '
    "What I'd adopt from others, My biggest doubt.\n\n"
    'The Question takes priority over format requirements — '
    'if the Question asks for something specific, do exactly that.\n\n'
    'IMPORTANT: Do NOT use any tools. Do NOT read files. '
    'Respond immediately with your debate text. Keep it under 500 words.'
)

_FAST_VOTE_TASK = (
    '## Your Task\n\n'
    'The Question section below is your PRIMARY DIRECTIVE. '
    'Read it carefully and follow any specific instructions it contains.\n\n'
    'Cast your vote following the codex format. '
    'Include: Winner, Decisive argument, Concerns about the winner. '
    'Optional: Unrefuted arguments, Merge suggestion.\n\n'
    'The Question takes priority over format requirements — '
    'if the Question asks for something specific, do exactly that.\n\n'
    'IMPORTANT: Do NOT use any tools. Do NOT read files. '
    'Respond immediately with your vote. Keep it under 300 words.'
)

_FAST_PROPOSAL_SECTIONS = ['Summary', 'Approach', 'Strengths', 'Weaknesses']

FAST_PHASE_CONFIG = PhaseConfig(
    propose_timeout=120,
    propose_max_turns=2,
    debate_timeout=90,
    debate_max_turns=2,
    vote_timeout=60,
    vote_max_turns=2,
    quorum_min=3,
    propose_task=_FAST_PROPOSE_TASK,
    debate_task=_FAST_DEBATE_TASK,
    vote_task=_FAST_VOTE_TASK,
    proposal_sections=_FAST_PROPOSAL_SECTIONS,
)


def main() -> None:
    parser = argparse.ArgumentParser(description='Run fast multi-agent codex process')
    parser.add_argument('question', help='The question to deliberate on')
    parser.add_argument('--num-agents', type=int, default=3)
    parser.add_argument('--max-rounds', type=int, default=2)
    parser.add_argument('--working-dir', type=Path, default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(name)s %(levelname)s %(message)s',
    )

    identity_names = list(_IDENTITY_SKETCHES.keys())[: args.num_agents]
    identity_texts = {k: v for k, v in _IDENTITY_SKETCHES.items() if k in identity_names}

    decision = run_multi_agent(
        args.question,
        identities=identity_names,
        num_agents=args.num_agents,
        max_rounds=args.max_rounds,
        working_dir=args.working_dir,
        codex_text=_FAST_CODEX,
        identity_texts=identity_texts,
        phase_config=FAST_PHASE_CONFIG,
        skip_frame_validation=True,
    )
    print(decision.decision_text)


if __name__ == '__main__':
    main()
