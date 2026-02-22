"""CLI entrypoint for the multi-agent orchestrator."""

import argparse
import logging
from pathlib import Path

from multi_agent.orchestrate import run_multi_agent


HELP_TEXT = """\
/multi-agent — Run a multi-agent decision process on a design question.

Usage:
  /multi-agent <question>
  /multi-agent help

What happens:
  1. The question is framed into a structured decision format
  2. Multiple agents with distinct identities propose solutions in parallel
  3. Agents debate each other's proposals
  4. Agents vote on the best approach
  5. If no consensus, the cycle repeats with refined framing (up to 3 rounds)
  6. A decision record is produced

Options:
  --num-agents N    Number of agents (default: 5)
  --max-rounds N    Maximum deliberation rounds (default: 3)
  --working-dir D   Output directory (default: auto-generated)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description='Run multi-agent codex process')
    parser.add_argument('question', help='The question to deliberate on')
    parser.add_argument('--num-agents', type=int, default=5)
    parser.add_argument('--max-rounds', type=int, default=3)
    parser.add_argument('--working-dir', type=Path, default=None)
    args = parser.parse_args()

    if args.question.strip().lower() == 'help':
        print(HELP_TEXT)
        return

    logging.basicConfig(
        level=logging.INFO,
        format='%(name)s %(levelname)s %(message)s',
    )
    decision = run_multi_agent(
        args.question,
        num_agents=args.num_agents,
        max_rounds=args.max_rounds,
        working_dir=args.working_dir,
    )
    print(decision.decision_text)


if __name__ == '__main__':
    main()
