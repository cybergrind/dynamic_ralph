"""CLI entrypoint for the multi-agent orchestrator."""

import argparse
import logging
import sys
from pathlib import Path

from multi_agent.orchestrate import run_multi_agent


def main() -> None:
    parser = argparse.ArgumentParser(description='Run multi-agent codex process')
    parser.add_argument('question', help='The question to deliberate on')
    parser.add_argument('--num-agents', type=int, default=5)
    parser.add_argument('--max-rounds', type=int, default=3)
    parser.add_argument('--working-dir', type=Path, default=None)
    args = parser.parse_args()

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
