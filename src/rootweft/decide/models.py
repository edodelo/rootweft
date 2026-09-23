"""Validated immutable decision values and local resource budgets."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self

from rootweft.models import GraphDocument


class ProviderError(ValueError):
    """A redacted decision failure; never includes remote bodies or state."""


class ProviderConfigurationError(ProviderError):
    """Invalid credentials, model selection, or budgets."""


class ProviderSchemaError(ProviderError):
    """A request or answer violates the typed decision contract."""


class RemoteRequiredError(ProviderError):
    """Remote failure carrying a usable graph for CLI exit code 5."""

    def __init__(self, graph: GraphDocument) -> None:
        self.graph = graph
        super().__init__("required remote adjudication failed")


def _text(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ProviderSchemaError("invalid decision text")


def _number(value: object, upper: float = 1) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= upper
    ):
        raise ProviderSchemaError("invalid finite numeric value")


def valid_model(model: object, expected: str) -> bool:
    if not isinstance(model, str):
        return False
    if expected == "jev-1.13.0":
        return model == expected
    if expected != "typesafe/jev-1.13":
        return False
    if model == expected:
        return True
    if re.fullmatch(r"typesafe/jev-1\.13-20[0-9]{6}", model) is None:
        return False
    try:
        datetime.strptime(model[-8:], "%Y%m%d")
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class NoulQuestion:
    id: str
    instructions: str

    def __post_init__(self) -> None:
        _text(self.id)
        _text(self.instructions)

    def to_wire(self) -> dict[str, Any]:
        return {"type": "noul", "instructions": self.instructions}


@dataclass(frozen=True)
class ChoiceQuestion:
    id: str
    instructions: str
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.id)
        _text(self.instructions)
        if not isinstance(self.options, (tuple, list)):
            raise ProviderSchemaError("invalid choice options")
        options = tuple(self.options)
        for option in options:
            _text(option)
        if not 1 <= len(options) <= 255 or len(set(options)) != len(options):
            raise ProviderSchemaError("invalid choice options")
        object.__setattr__(self, "options", options)

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict.fromkeys(self.options),
        }


@dataclass(frozen=True)
class ScoreQuestion:
    id: str
    instructions: str
    levels: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.id)
        _text(self.instructions)
        if (
            not isinstance(self.levels, (tuple, list))
            or not 2 <= len(self.levels) <= 10
        ):
            raise ProviderSchemaError("score requires two to ten levels")
        for level in self.levels:
            _text(level)
        object.__setattr__(self, "levels", tuple(self.levels))

    def to_wire(self) -> dict[str, Any]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.levels),
        }


Question = NoulQuestion | ChoiceQuestion | ScoreQuestion


@dataclass(frozen=True)
class ProviderAnswer:
    question_id: str
    kind: Literal["noul", "choice", "score"]
    value: float | str
    model: str
    probabilities: Mapping[str, float] = field(default_factory=dict)
    confidence: float | None = None

    def __post_init__(self) -> None:
        _text(self.question_id)
        if not (
            valid_model(self.model, "jev-1.13.0")
            or valid_model(self.model, "typesafe/jev-1.13")
        ):
            raise ProviderSchemaError("unexpected response model")
        if not isinstance(self.probabilities, Mapping):
            raise ProviderSchemaError("invalid probability distribution")
        probabilities = dict(self.probabilities)
        if self.kind == "noul":
            _number(self.value)
            if probabilities or self.confidence is not None:
                raise ProviderSchemaError("unexpected noul fields")
        elif self.kind in {"choice", "score"}:
            _number(self.confidence)
            if not 1 <= len(probabilities) <= 255:
                raise ProviderSchemaError("invalid probability distribution")
            for key, probability in probabilities.items():
                _text(key)
                _number(probability)
            if not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-6):
                raise ProviderSchemaError("probabilities must sum to one")
            if self.kind == "choice":
                _text(self.value)
                if self.value not in probabilities:
                    raise ProviderSchemaError("choice is absent from distribution")
                if probabilities[self.value] < max(probabilities.values()):
                    raise ProviderSchemaError("choice contradicts distribution")
            else:
                _number(self.value, 9)
        else:
            raise ProviderSchemaError("unknown answer type")
        object.__setattr__(self, "probabilities", MappingProxyType(probabilities))

    @classmethod
    def noul(cls, probability: float, model: str, question_id: str = "q") -> Self:
        return cls(question_id, "noul", probability, model)

    @classmethod
    def choice(
        cls,
        question_id: str,
        choice: str,
        probabilities: Mapping[str, float],
        confidence: float,
        model: str,
    ) -> Self:
        return cls(question_id, "choice", choice, model, probabilities, confidence)

    @classmethod
    def score(
        cls,
        question_id: str,
        score: float,
        probabilities: Mapping[str, float],
        confidence: float,
        model: str,
    ) -> Self:
        return cls(question_id, "score", score, model, probabilities, confidence)


def validate_answer(question: Question, answer: ProviderAnswer) -> None:
    if not isinstance(answer, ProviderAnswer) or question.id != answer.question_id:
        raise ProviderSchemaError("answer ID mismatch")
    if question.to_wire()["type"] != answer.kind:
        raise ProviderSchemaError("answer type mismatch")
    if isinstance(question, ChoiceQuestion):
        if set(answer.probabilities) != set(question.options):
            raise ProviderSchemaError("answer option mismatch")
    if isinstance(question, ScoreQuestion):
        if set(answer.probabilities) != {str(i) for i in range(len(question.levels))}:
            raise ProviderSchemaError("answer level mismatch")
        _number(answer.value, len(question.levels) - 1)
        weighted = sum(int(key) * value for key, value in answer.probabilities.items())
        # The documented OpenRouter response rounds probabilities and score
        # independently to two decimals. Bound the accumulated rounding error.
        tolerance = 0.005 * (1 + sum(range(len(question.levels)))) + 1e-6
        if not math.isclose(float(answer.value), weighted, abs_tol=tolerance):
            raise ProviderSchemaError("score contradicts distribution")


def validate_answers(
    questions: tuple[Question, ...], answers: tuple[ProviderAnswer, ...], model: str
) -> tuple[ProviderAnswer, ...]:
    if not isinstance(answers, tuple) or not all(
        isinstance(a, ProviderAnswer) for a in answers
    ):
        raise ProviderSchemaError("invalid answer collection")
    ids = [a.question_id for a in answers]
    if len(ids) != len(set(ids)) or set(ids) != {q.id for q in questions}:
        raise ProviderSchemaError("answer IDs must match questions exactly")
    by_id = {a.question_id: a for a in answers}
    for question in questions:
        answer = by_id[question.id]
        if not valid_model(answer.model, model):
            raise ProviderSchemaError("unexpected response model")
        validate_answer(question, answer)
    return tuple(by_id[q.id] for q in questions)


class DecisionProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def decide(
        self, state: str, questions: tuple[Question, ...]
    ) -> tuple[ProviderAnswer, ...]: ...


@dataclass(frozen=True)
class DecisionPolicy:
    """One batch, bounded retries; cost is a conservative estimate, not billing."""

    max_questions: int = 32
    max_options: int = 255
    max_payload_bytes: int = 64_000
    max_response_bytes: int = 256_000
    max_cost_usd: float = 0.10
    input_price_per_million: float = 1.0
    max_retries: int = 2
    timeout_seconds: float = 15
    max_retry_delay_seconds: float = 2

    def __post_init__(self) -> None:
        for value, limit in (
            (self.max_questions, 128),
            (self.max_options, 255),
            (self.max_payload_bytes, 1_000_000),
            (self.max_response_bytes, 2_000_000),
        ):
            if type(value) is not int or not 1 <= value <= limit:
                raise ProviderConfigurationError("invalid decision budget")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 3:
            raise ProviderConfigurationError("invalid retry budget")
        for amount, ceiling in (
            (self.max_cost_usd, 100),
            (self.input_price_per_million, 100),
            (self.timeout_seconds, 30),
            (self.max_retry_delay_seconds, 60),
        ):
            try:
                _number(amount, ceiling)
                if amount <= 0:
                    raise ProviderSchemaError("positive budget required")
            except ProviderSchemaError:
                raise ProviderConfigurationError("invalid decision budget") from None


@dataclass(frozen=True)
class EgressPreview:
    candidate_count: int
    question_count: int
    paths: tuple[str, ...]
    estimated_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "paths", tuple(self.paths))

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "question_count": self.question_count,
            "paths": list(self.paths),
            "estimated_bytes": self.estimated_bytes,
        }
