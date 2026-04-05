"""Tests for multi_agent.extract module."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from multi_agent.backend import AgentResult
from multi_agent.extract import ExtractionResult, extract


# ---------------------------------------------------------------------------
# Test model
# ---------------------------------------------------------------------------


class SimpleModel(BaseModel):
    """Minimal model for testing extraction."""

    winner: str
    reason: str

    @field_validator('winner')
    @classmethod
    def winner_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('winner must not be empty')
        return v.strip()


class ReviewOutput(BaseModel):
    """Non-vote model to prove the mechanism is model-agnostic."""

    summary: str
    verdict: str
    critical_issues: str = ''


def _good_text() -> str:
    return '## Winner\nA\n\n## Reason\nStrong argument'


def _bad_text() -> str:
    return 'No headings here, just rambling text.'


# ---------------------------------------------------------------------------
# ExtractionResult
# ---------------------------------------------------------------------------


class TestExtractionResult:
    def test_succeeded_true_when_value(self) -> None:
        r = ExtractionResult(value='x', attempts=[])
        assert r.succeeded is True

    def test_succeeded_false_when_none(self) -> None:
        r = ExtractionResult(value=None, attempts=[])
        assert r.succeeded is False


# ---------------------------------------------------------------------------
# extract — core loop
# ---------------------------------------------------------------------------


class TestExtract:
    def test_succeeds_first_try(self) -> None:
        """Valid text parses on first attempt, no invoke needed."""
        result = extract(_good_text(), SimpleModel)
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'A'
        assert result.value.reason == 'Strong argument'
        assert len(result.attempts) == 1
        assert result.attempts[0].succeeded

    def test_retries_on_validation_error(self) -> None:
        """Bad text triggers retry; invoke returns good text."""
        invoked = []

        def fake_invoke(prompt: str) -> AgentResult:
            invoked.append(prompt)
            return AgentResult(exit_code=0, full_response=_good_text())

        result = extract(
            _bad_text(),
            SimpleModel,
            prompt='original prompt',
            invoke=fake_invoke,
            max_attempts=2,
        )
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'A'
        assert len(result.attempts) == 2
        assert not result.attempts[0].succeeded
        assert result.attempts[1].succeeded
        assert len(invoked) == 1

    def test_exhausts_attempts(self) -> None:
        """All attempts fail -> value is None."""

        def fake_invoke(prompt: str) -> AgentResult:
            return AgentResult(exit_code=0, full_response=_bad_text())

        result = extract(
            _bad_text(),
            SimpleModel,
            prompt='p',
            invoke=fake_invoke,
            max_attempts=3,
        )
        assert not result.succeeded
        assert result.value is None
        assert len(result.attempts) == 3
        assert all(not a.succeeded for a in result.attempts)

    def test_no_invoke_skips_retry(self) -> None:
        """Without invoke, fails after first attempt."""
        result = extract(_bad_text(), SimpleModel)
        assert not result.succeeded
        assert len(result.attempts) == 1

    def test_correction_prompt_contains_errors(self) -> None:
        """Invoke receives correction prompt with error details."""
        captured_prompt = []

        def fake_invoke(prompt: str) -> AgentResult:
            captured_prompt.append(prompt)
            return AgentResult(exit_code=0, full_response=_good_text())

        extract(
            _bad_text(),
            SimpleModel,
            prompt='original prompt',
            invoke=fake_invoke,
            max_attempts=2,
        )
        assert len(captured_prompt) == 1
        assert 'CORRECTION REQUIRED' in captured_prompt[0]
        assert 'original prompt' in captured_prompt[0]
        assert _bad_text()[:100] in captured_prompt[0]

    def test_custom_extract_fn(self) -> None:
        """Custom extract_fn is used instead of default."""
        custom_called = []

        def custom_extract(text: str) -> SimpleModel:
            custom_called.append(text)
            return SimpleModel(winner='Z', reason='custom')

        result = extract('anything', SimpleModel, extract_fn=custom_extract)
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'Z'
        assert len(custom_called) == 1

    def test_structured_output_used_when_provided(self) -> None:
        """structured_output is validated directly — no markdown parsing needed."""
        result = extract(
            'unparseable garbage',
            SimpleModel,
            structured_output={'winner': 'B', 'reason': 'speed'},
        )
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'B'
        assert result.value.reason == 'speed'

    def test_structured_output_none_falls_back_to_markdown(self) -> None:
        """When structured_output is None, existing markdown extraction runs."""
        result = extract(_good_text(), SimpleModel, structured_output=None)
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'A'

    def test_structured_output_invalid_falls_back_to_markdown(self) -> None:
        """Invalid structured_output falls back to markdown extraction."""
        result = extract(
            _good_text(),
            SimpleModel,
            structured_output={'winner': '', 'reason': 'x'},  # empty winner fails validator
        )
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'A'  # recovered via markdown

    def test_structured_output_skips_retry(self) -> None:
        """When structured_output validates, invoke is never called."""
        invoked = []

        def fake_invoke(prompt: str) -> AgentResult:
            invoked.append(prompt)
            return AgentResult(exit_code=0, full_response=_good_text())

        result = extract(
            'garbage',
            SimpleModel,
            structured_output={'winner': 'C', 'reason': 'solid'},
            prompt='p',
            invoke=fake_invoke,
        )
        assert result.succeeded
        assert result.value is not None
        assert result.value.winner == 'C'
        assert len(invoked) == 0

    def test_structured_output_with_arbitrary_pydantic_model(self) -> None:
        """Any Pydantic BaseModel works — not limited to vote models."""
        result = extract(
            'irrelevant text',
            ReviewOutput,
            structured_output={'summary': 'Clean code', 'verdict': 'approve'},
        )
        assert result.succeeded
        assert result.value is not None
        assert result.value.summary == 'Clean code'
        assert result.value.verdict == 'approve'
        assert result.value.critical_issues == ''  # default

    def test_arbitrary_model_validation_catches_missing_required(self) -> None:
        """Pydantic validation is enforced for any model — missing 'verdict' fails."""
        result = extract(
            'no headings',
            ReviewOutput,
            structured_output={'summary': 'ok'},  # missing required 'verdict'
        )
        # structured_output fails, markdown also fails → not succeeded
        assert not result.succeeded

    def test_default_extract_derives_headings(self) -> None:
        """Field names are converted to headings: 'winner' -> 'Winner',
        'decisive_argument' -> 'Decisive argument'."""

        class MultiWordModel(BaseModel):
            decisive_argument: str
            merge_suggestion: str

        text = '## Decisive argument\nGood point\n\n## Merge suggestion\nCombine A and B'
        result = extract(text, MultiWordModel)
        assert result.succeeded
        assert result.value is not None
        assert result.value.decisive_argument == 'Good point'
        assert result.value.merge_suggestion == 'Combine A and B'
