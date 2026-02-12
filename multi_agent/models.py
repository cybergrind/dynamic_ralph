"""Pydantic models for prd.json validation and data access."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class UserStory(BaseModel):
    id: str
    title: str
    description: str
    acceptanceCriteria: list[str]
    priority: Annotated[int, 'positive, matches array index + 1']
    passes: bool
    notes: str
    depends_on: list[str] = Field(default_factory=list)

    @field_validator('id')
    @classmethod
    def id_format(cls, v: str) -> str:
        if not re.match(r'^US-\d{3}$', v):
            raise ValueError(f"must match 'US-NNN' format, got: '{v}'")
        return v

    @field_validator('acceptanceCriteria')
    @classmethod
    def criteria_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError('must not be empty')
        return v

    @field_validator('priority')
    @classmethod
    def priority_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError('must be positive')
        return v


class Prd(BaseModel):
    project: str
    branchName: str
    description: str
    userStories: list[UserStory]

    @field_validator('branchName')
    @classmethod
    def branch_format(cls, v: str) -> str:
        if not re.match(r'^ralph/[a-z0-9]+(?:-[a-z0-9]+)*$', v):
            raise ValueError(f"must match 'ralph/kebab-case', got: '{v}'")
        return v

    @field_validator('userStories')
    @classmethod
    def stories_not_empty(cls, v: list[UserStory]) -> list[UserStory]:
        if not v:
            raise ValueError('must not be empty')
        return v

    @model_validator(mode='after')
    def sequential_ids_and_priorities(self) -> Prd:
        errors: list[str] = []
        seen_ids: set[str] = set()
        for i, story in enumerate(self.userStories):
            expected_id = f'US-{i + 1:03d}'
            if story.id != expected_id:
                errors.append(f"userStories[{i}].id: expected '{expected_id}', got '{story.id}'")
            if story.id in seen_ids:
                errors.append(f"userStories[{i}].id: duplicate '{story.id}'")
            seen_ids.add(story.id)

            if story.priority != i + 1:
                errors.append(f'userStories[{i}].priority: expected {i + 1}, got {story.priority}')
        if errors:
            raise ValueError('; '.join(errors))
        return self


class FlatStory(BaseModel):
    """Legacy flat-array story format with lenient validation."""

    id: str | None = None
    title: str | None = None
    description: str | None = None
    passes: bool | None = None
    acceptanceCriteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


def parse_prd(data: object) -> Prd | list[FlatStory]:
    """Parse raw JSON data into a Prd or list of FlatStory.

    Raises ValidationError on invalid data.
    """
    if isinstance(data, list):
        return [FlatStory.model_validate(item) for item in data]
    return Prd.model_validate(data)
