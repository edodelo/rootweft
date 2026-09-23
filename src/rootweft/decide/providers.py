"""BYOK fixed-origin clients for the official typed Decisions contracts."""

from __future__ import annotations

import json
import math
import os
import ssl
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from rootweft.decide.models import (
    ChoiceQuestion,
    DecisionPolicy,
    NoulQuestion,
    ProviderAnswer,
    ProviderConfigurationError,
    ProviderError,
    ProviderSchemaError,
    Question,
    ScoreQuestion,
    _number,
    valid_model,
    validate_answers,
)
from rootweft.secrets import find_secrets


def canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def inspect_text(value: str) -> None:
    if find_secrets(value):
        raise ProviderError("egress blocked by secret signals")


def request_payload(
    state: str, questions: tuple[Question, ...], model: str, policy: DecisionPolicy
) -> bytes:
    if not isinstance(state, str) or not state or not isinstance(questions, tuple):
        raise ProviderSchemaError("invalid state or questions")
    if not 1 <= len(questions) <= policy.max_questions:
        raise ProviderError("question budget exceeded")
    if not all(
        type(q) in (NoulQuestion, ChoiceQuestion, ScoreQuestion) for q in questions
    ):
        raise ProviderSchemaError("invalid question")
    if len({q.id for q in questions}) != len(questions):
        raise ProviderSchemaError("duplicate question IDs")
    inspect_text(state)
    for question in questions:
        inspect_text(question.id)
        inspect_text(question.instructions)
        for label in (
            question.options
            if isinstance(question, ChoiceQuestion)
            else question.levels
            if isinstance(question, ScoreQuestion)
            else ()
        ):
            inspect_text(label)
        if (
            isinstance(question, ChoiceQuestion)
            and len(question.options) > policy.max_options
        ):
            raise ProviderError("option budget exceeded")
    body: dict[str, Any] = {
        "model": model,
        "state": state,
        "questions": {q.id: q.to_wire() for q in questions},
    }
    if model == "typesafe/jev-1.13":
        body["provider"] = {"only": ["TypeSafe"], "allow_fallbacks": False}
    encoded = canonical(body).encode("utf-8")
    inspect_text(encoded.decode("utf-8"))
    if len(encoded) > policy.max_payload_bytes:
        raise ProviderError("payload budget exceeded")
    # One token per byte plus fixed overhead, reserving every possible retry.
    estimate = (len(encoded) + 1024) * policy.input_price_per_million / 1_000_000
    if estimate * (1 + policy.max_retries) > policy.max_cost_usd:
        raise ProviderError("estimated cost budget exceeded")
    return encoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderSchemaError("duplicate response field")
        result[key] = value
    return result


def _parse(
    content: bytes, questions: tuple[Question, ...], model: str, policy: DecisionPolicy
) -> tuple[ProviderAnswer, ...]:
    try:
        body = json.loads(content, object_pairs_hook=_unique_object)
        if not isinstance(body, dict) or not valid_model(body.get("model"), model):
            raise ProviderSchemaError("unexpected response model")
        raw_answers = body["answers"]
        if not isinstance(raw_answers, dict) or set(raw_answers) != {
            q.id for q in questions
        }:
            raise ProviderSchemaError("answer IDs must match questions exactly")
        usage = body["usage"]
        if not isinstance(usage, dict):
            raise ProviderSchemaError("invalid usage")
        for name in ("input_tokens", "output_tokens"):
            if type(usage.get(name)) is not int or usage[name] < 0:
                raise ProviderSchemaError("invalid token usage")
        if "cost" in usage:
            _number(usage["cost"], policy.max_cost_usd)
        answers = []
        for question in questions:
            raw = raw_answers[question.id]
            if (
                not isinstance(raw, dict)
                or raw.get("type") != question.to_wire()["type"]
            ):
                raise ProviderSchemaError("answer type mismatch")
            kind = raw["type"]
            if kind == "noul":
                answer = ProviderAnswer.noul(raw["noul"], body["model"], question.id)
            elif kind == "choice":
                answer = ProviderAnswer.choice(
                    question.id,
                    raw["choice"],
                    raw["probabilities"],
                    raw["confidence"],
                    body["model"],
                )
            else:
                assert isinstance(question, ScoreQuestion)
                if raw.get("legend") != {
                    str(i): v for i, v in enumerate(question.levels)
                }:
                    raise ProviderSchemaError("score legend mismatch")
                answer = ProviderAnswer.score(
                    question.id,
                    raw["score"],
                    raw["probabilities"],
                    raw["confidence"],
                    body["model"],
                )
            answers.append(answer)
        return validate_answers(questions, tuple(answers), model)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ProviderSchemaError("invalid provider response") from None


def _tls_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    # Some transports drop SSL causes, but preserve this standard error marker.
    return "CERTIFICATE_VERIFY_FAILED" in str(error) or "SSL" in str(error)


def _retry_delay(header: str | None, attempt: int, cap: float) -> float:
    delay = min(0.25 * 2**attempt, cap)
    if header:
        try:
            delay = float(header)
        except ValueError:
            try:
                delay = (
                    parsedate_to_datetime(header) - datetime.now(UTC)
                ).total_seconds()
            except (ValueError, TypeError, OverflowError):
                pass
        if not math.isfinite(delay) or delay < 0:
            delay = cap
        if delay > cap:
            raise ProviderError("retry delay exceeds budget")
    return float(delay)


class TypeSafeProvider:
    """Explicit TypeSafe key, fixed HTTPS origin, no environment proxies."""

    name = "typesafe"
    model = "jev-1.13.0"
    reproducible = True
    _endpoint = "https://api.typesafe.ai/v1/systemone"
    _key_env = "TYPESAFE_API_KEY"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        policy: DecisionPolicy = DecisionPolicy(),
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if model is not None and model != self.model:
            raise ProviderConfigurationError("provider requires its fixed model")
        key = os.environ.get(self._key_env) if api_key is None else api_key
        if (
            not isinstance(key, str)
            or not key
            or any(ord(c) < 33 or ord(c) > 126 for c in key)
        ):
            raise ProviderConfigurationError("valid provider API key required")
        self._api_key = key
        self._policy = policy
        self._transport = transport
        self._sleep = sleep

    def decide(
        self, state: str, questions: tuple[Question, ...]
    ) -> tuple[ProviderAnswer, ...]:
        payload = request_payload(state, questions, self.model, self._policy)
        content: bytes | None = None
        try:
            with httpx.Client(
                verify=True,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(self._policy.timeout_seconds),
                transport=self._transport,
            ) as client:
                for attempt in range(self._policy.max_retries + 1):
                    deadline = time.monotonic() + self._policy.timeout_seconds
                    delay = min(0.25 * 2**attempt, self._policy.max_retry_delay_seconds)
                    try:
                        with client.stream(
                            "POST",
                            self._endpoint,
                            content=payload,
                            headers={
                                "Authorization": f"Bearer {self._api_key}",
                                "Content-Type": "application/json",
                                "Accept-Encoding": "identity",
                            },
                        ) as response:
                            if response.status_code == 200:
                                if (
                                    response.headers.get(
                                        "Content-Encoding", "identity"
                                    ).lower()
                                    != "identity"
                                ):
                                    raise ProviderSchemaError(
                                        "compressed response rejected"
                                    )
                                chunks = bytearray()
                                for chunk in response.iter_bytes():
                                    if time.monotonic() > deadline:
                                        raise ProviderError(
                                            "response deadline exceeded"
                                        )
                                    if (
                                        len(chunks) + len(chunk)
                                        > self._policy.max_response_bytes
                                    ):
                                        raise ProviderSchemaError(
                                            "response budget exceeded"
                                        )
                                    chunks.extend(chunk)
                                content = bytes(chunks)
                                break
                            transient = response.status_code in {
                                429,
                                500,
                                502,
                                503,
                                504,
                                529,
                            }
                            if not transient or attempt == self._policy.max_retries:
                                raise ProviderError("provider HTTP failure")
                            delay = _retry_delay(
                                response.headers.get("Retry-After"),
                                attempt,
                                self._policy.max_retry_delay_seconds,
                            )
                    except (httpx.TimeoutException, httpx.ConnectError) as error:
                        if _tls_error(error) or attempt == self._policy.max_retries:
                            raise ProviderError("provider transport failure") from None
                    self._sleep(delay)
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise ProviderError("provider transport failure") from None
        if content is None:
            raise ProviderError("provider returned no response")
        return _parse(content, questions, self.model, self._policy)


class OpenRouterExperimentalProvider(TypeSafeProvider):
    """Experimental route: provider-controlled dated snapshots are not immutable."""

    name = "openrouter-experimental"
    model = "typesafe/jev-1.13"
    reproducible = False
    _endpoint = "https://openrouter.ai/api/alpha/decisions"
    _key_env = "OPENROUTER_API_KEY"
