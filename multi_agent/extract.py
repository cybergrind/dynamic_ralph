"""Validated agent output extraction with retry-and-refine.

The ONE building block for all structured agent interactions. Given agent
text output and a Pydantic model, extracts and validates structured data.
On validation failure, re-invokes the agent with error feedback appended
to the original prompt.

Usage::

    result = extract(text, VoteOutput, prompt=original_prompt, invoke=run_fn)
    if result.succeeded:
        vote = result.value
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from multi_agent.parsing import parse_sections


if TYPE_CHECKING:
    from collections.abc import Callable

    from multi_agent.backend import AgentResult

log = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    """Record of a single extraction attempt."""

    raw_text: str
    errors: list[str]
    succeeded: bool


@dataclass
class ExtractionResult(Generic[T]):
    """Result of extract(), carrying the validated model or failure diagnostics."""

    value: T | None
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.value is not None


# ---------------------------------------------------------------------------
# Default extractor
# ---------------------------------------------------------------------------


def _field_to_heading(name: str) -> str:
    """Convert a Python field name to a markdown heading.

    ``decisive_argument`` → ``Decisive argument``
    ``winner`` → ``Winner``
    """
    return name.replace('_', ' ').capitalize()


def _default_extract(text: str, model_cls: type[T]) -> T:
    """Extract by parsing markdown sections derived from model field names."""
    field_names = list(model_cls.model_fields.keys())
    heading_map = {name: _field_to_heading(name) for name in field_names}

    required = list(heading_map.values())
    sections, _diag = parse_sections(text, required=required)

    data: dict[str, str] = {}
    for field_name, heading in heading_map.items():
        if heading in sections:
            data[field_name] = sections[heading]

    return model_cls.model_validate(data)


# ---------------------------------------------------------------------------
# Correction prompt
# ---------------------------------------------------------------------------


def _build_correction_prompt(original_prompt: str, raw_text: str, errors: list[str]) -> str:
    """Append validation errors to the original prompt for retry."""
    error_block = '\n'.join(f'- {e}' for e in errors)
    return (
        f'{original_prompt}\n\n'
        f'---\n\n'
        f'## CORRECTION REQUIRED\n\n'
        f'Your previous response could not be parsed. Errors:\n\n'
        f'{error_block}\n\n'
        f'Your previous output (first 2000 chars):\n\n'
        f'```\n{raw_text[:2000]}\n```\n\n'
        f'Please respond again with the EXACT required format.'
    )


def _format_errors(exc: ValidationError) -> list[str]:
    """Convert Pydantic ValidationError into human-readable error strings."""
    errors = []
    for e in exc.errors():
        loc = ' -> '.join(str(x) for x in e['loc']) if e['loc'] else '(root)'
        errors.append(f'{loc}: {e["msg"]}')
    return errors


# ---------------------------------------------------------------------------
# Core extraction loop
# ---------------------------------------------------------------------------


def extract(
    text: str,
    model_cls: type[T],
    *,
    extract_fn: Callable[[str], T] | None = None,
    prompt: str | None = None,
    invoke: Callable[[str], AgentResult] | None = None,
    max_attempts: int = 2,
) -> ExtractionResult[T]:
    """Extract and validate structured data from agent text.

    Parameters
    ----------
    text:
        The agent's raw text output to parse.
    model_cls:
        The Pydantic model class to validate against.
    extract_fn:
        Custom extraction function ``(text) -> model_instance``.
        Defaults to markdown-section-based extraction.
    prompt:
        The original prompt sent to the agent. Required when *invoke*
        is provided (used to build correction prompts for retries).
    invoke:
        Callable that re-runs the agent with a new prompt and returns
        ``AgentResult``. If ``None``, no retries are attempted.
    max_attempts:
        Total attempts including the initial parse. Default 2 (one retry).

    Returns
    -------
    ExtractionResult[T] with ``.value`` set on success, ``None`` on exhaustion.
    """
    attempts: list[Attempt] = []
    current_text = text

    for attempt_num in range(max_attempts):
        try:
            if extract_fn is not None:
                value = extract_fn(current_text)
            else:
                value = _default_extract(current_text, model_cls)
            attempts.append(Attempt(raw_text=current_text, errors=[], succeeded=True))
            return ExtractionResult(value=value, attempts=attempts)
        except (ValidationError, ValueError, KeyError) as exc:
            if isinstance(exc, ValidationError):
                errors = _format_errors(exc)
            else:
                errors = [str(exc)]

            attempts.append(Attempt(raw_text=current_text, errors=errors, succeeded=False))
            log.warning(
                'Extraction attempt %d/%d failed: %s',
                attempt_num + 1,
                max_attempts,
                errors,
            )

            # Can we retry?
            is_last = attempt_num >= max_attempts - 1
            if is_last or invoke is None or prompt is None:
                break

            correction_prompt = _build_correction_prompt(prompt, current_text, errors)
            agent_result = invoke(correction_prompt)
            current_text = agent_result.full_response

    return ExtractionResult(value=None, attempts=attempts)
