"""PRD file I/O and story navigation."""

import json
from pathlib import Path

from multi_agent.models import parse_prd, Prd


def load_prd(path: Path) -> list[dict]:
    """Load stories from prd.json, supporting both rich and flat formats."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data['userStories']
    return data


def load_prd_model(path: Path) -> Prd | list:
    """Load and validate prd.json via Pydantic models."""
    with open(path) as f:
        data = json.load(f)
    return parse_prd(data)


def save_prd(path: Path, stories: list[dict]) -> None:
    with open(path, 'w') as f:
        json.dump(stories, f, indent=2)
        f.write('\n')


def find_next_story(stories: list[dict]) -> dict | None:
    for story in stories:
        if story.get('passes') is not True:
            return story
    return None
