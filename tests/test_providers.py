import json
import socket
import ssl
import traceback

import httpx
import pytest

from rootweft.decide.models import (
    ChoiceQuestion,
    DecisionPolicy,
    NoulQuestion,
    ProviderConfigurationError,
    ProviderError,
    ProviderSchemaError,
    ScoreQuestion,
)
from rootweft.decide.providers import OpenRouterExperimentalProvider, TypeSafeProvider

QUESTIONS = (
    NoulQuestion("n", "True?"),
    ChoiceQuestion("c", "Choose", ("a", "b")),
    ScoreQuestion("s", "Rate", ("low", "high")),
)


def response(model="jev-1.13.0"):
    return {
        "model": model,
        "answers": {
            "n": {"type": "noul", "noul": 0.8},
            "c": {
                "type": "choice",
                "choice": "a",
                "confidence": 0.6,
                "probabilities": {"a": 0.8, "b": 0.2},
            },
            "s": {
                "type": "score",
                "score": 0.7,
                "confidence": 0.4,
                "probabilities": {"0": 0.3, "1": 0.7},
                "legend": {"0": "low", "1": "high"},
            },
        },
        "usage": {"input_tokens": 150, "output_tokens": 20},
    }


@pytest.mark.parametrize(
    "provider_class,url,model,returned",
    [
        (
            TypeSafeProvider,
            "https://api.typesafe.ai/v1/systemone",
            "jev-1.13.0",
            "jev-1.13.0",
        ),
        (
            OpenRouterExperimentalProvider,
            "https://openrouter.ai/api/alpha/decisions",
            "typesafe/jev-1.13",
            "typesafe/jev-1.13-20260917",
        ),
    ],
)
def test_official_wire_contract(provider_class, url, model, returned):
    def handler(request):
        assert str(request.url) == url
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["state"] == '{"safe":true}'
        assert payload["model"] == model
        assert payload["questions"] == {
            "n": {"type": "noul", "instructions": "True?"},
            "c": {
                "type": "choice",
                "instructions": "Choose",
                "criteria": {"a": None, "b": None},
            },
            "s": {"type": "score", "instructions": "Rate", "criteria": ["low", "high"]},
        }
        assert request.extensions["timeout"]["read"] <= 30
        if provider_class is OpenRouterExperimentalProvider:
            assert payload["provider"] == {
                "only": ["TypeSafe"],
                "allow_fallbacks": False,
            }
        return httpx.Response(200, json=response(returned))

    provider = provider_class(
        api_key="test-key", transport=httpx.MockTransport(handler)
    )
    answers = provider.decide('{"safe":true}', QUESTIONS)
    assert [answer.question_id for answer in answers] == ["n", "c", "s"]
    assert answers[0].model == returned
    assert "test-key" not in repr(provider)


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview", "other", ""])
def test_direct_model_must_be_immutable(model):
    with pytest.raises(ProviderConfigurationError):
        TypeSafeProvider(api_key="test-key", model=model)


def test_key_required_without_network(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ProviderConfigurationError):
        TypeSafeProvider()


@pytest.mark.parametrize(
    "status,attempts",
    [
        (429, 3),
        (500, 3),
        (503, 3),
        (529, 3),
        (501, 1),
        (505, 1),
        (400, 1),
        (401, 1),
        (403, 1),
        (422, 1),
        (302, 1),
    ],
)
def test_retry_is_transient_only_and_redirects_never_followed(status, attempts):
    calls, sleeps = [], []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            status,
            text="test-key private-state",
            headers={"Retry-After": "0.01", "Location": "https://attacker.example/"},
        )

    provider = TypeSafeProvider(
        api_key="test-key", transport=httpx.MockTransport(handler), sleep=sleeps.append
    )
    state = "private-state"
    with pytest.raises(ProviderError) as caught:
        provider.decide(state, QUESTIONS)
    assert len(calls) == attempts
    assert set(calls) == {"https://api.typesafe.ai/v1/systemone"}
    assert len(sleeps) == attempts - 1
    assert "test-key" not in str(caught.value)
    assert "private-state" not in "".join(traceback.format_exception(caught.value))
    if status == 429:
        assert sleeps == [0.01, 0.01]


@pytest.mark.parametrize("kind,attempts", [("timeout", 3), ("dns", 3), ("tls", 1)])
def test_transport_errors_are_bounded_and_redacted(kind, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("test-key private-state")
        cause = (
            ssl.SSLCertVerificationError("test-key")
            if kind == "tls"
            else socket.gaierror("test-key")
        )
        raise httpx.ConnectError("test-key private-state") from cause

    provider = TypeSafeProvider(
        api_key="test-key", transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    with pytest.raises(ProviderError) as caught:
        provider.decide("private-state", QUESTIONS)
    assert len(calls) == attempts
    assert "test-key" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "mutation",
    ["invalid_json", "missing", "extra", "model", "type", "legend", "nan", "cost"],
)
def test_schema_errors_never_retry(mutation):
    body = response()
    if mutation == "missing":
        del body["answers"]["n"]
    if mutation == "extra":
        body["answers"]["x"] = body["answers"]["n"]
    if mutation == "model":
        body["model"] = "jev-latest"
    if mutation == "type":
        body["answers"]["n"]["type"] = "score"
    if mutation == "legend":
        body["answers"]["s"]["legend"]["1"] = "untrusted"
    if mutation == "nan":
        body["answers"]["n"]["noul"] = float("nan")
    if mutation == "cost":
        body["usage"]["cost"] = -1
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            content=b"{" if mutation == "invalid_json" else json.dumps(body).encode(),
        )

    with pytest.raises(ProviderSchemaError):
        TypeSafeProvider(
            api_key="test-key", transport=httpx.MockTransport(handler)
        ).decide("safe", QUESTIONS)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "policy",
    [
        DecisionPolicy(max_questions=1),
        DecisionPolicy(max_payload_bytes=20),
        DecisionPolicy(max_cost_usd=0.000000001),
    ],
)
def test_request_budgets_reject_before_transport(policy):
    def handler(request):
        pytest.fail("budget overflow reached network")

    with pytest.raises(ProviderError):
        TypeSafeProvider(
            api_key="test-key", policy=policy, transport=httpx.MockTransport(handler)
        ).decide("safe", QUESTIONS)


def test_http_client_disables_environment_proxy_and_requires_tls(monkeypatch):
    original = httpx.Client
    seen = {}

    def client(**kwargs):
        seen.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=response()))
    TypeSafeProvider(api_key="test-key", transport=transport).decide("safe", QUESTIONS)
    assert seen["verify"] is True
    assert seen["trust_env"] is False
    assert seen["follow_redirects"] is False


def test_spoofed_question_type_cannot_send_uninspected_payload():
    class NoulQuestion:
        id = "q"
        instructions = "safe"

        def to_wire(self):
            return {"type": "noul", "instructions": "leaked private data"}

    calls = []
    transport = httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, json=response())
    )
    with pytest.raises(ProviderSchemaError):
        TypeSafeProvider(api_key="test-key", transport=transport).decide(
            "safe", (NoulQuestion(),)
        )
    assert calls == []


def test_compressed_response_rejected_before_body_consumption():
    class Unreadable(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("compressed body was consumed")
            yield b""

    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=Unreadable()
        )
    )
    with pytest.raises(ProviderSchemaError):
        TypeSafeProvider(api_key="test-key", transport=transport).decide(
            "safe", QUESTIONS
        )


def test_response_stream_stops_at_byte_budget():
    class Large(httpx.SyncByteStream):
        def __iter__(self):
            yield b"x" * 21
            pytest.fail("read past response budget")

    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Large()))
    with pytest.raises(ProviderSchemaError):
        TypeSafeProvider(
            api_key="test-key",
            policy=DecisionPolicy(max_response_bytes=20),
            transport=transport,
        ).decide("safe", QUESTIONS)


def test_response_stream_has_overall_deadline(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("rootweft.decide.providers.time.monotonic", lambda: now[0])

    class Slow(httpx.SyncByteStream):
        def __iter__(self):
            now[0] = 2.0
            yield b" "
            pytest.fail("continued reading beyond deadline")

    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Slow()))
    with pytest.raises(ProviderError):
        TypeSafeProvider(api_key="test-key", policy=DecisionPolicy(timeout_seconds=1, max_retries=0), transport=transport).decide("safe", QUESTIONS)


def test_retry_after_exceeding_budget_never_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    with pytest.raises(ProviderError):
        TypeSafeProvider(api_key="test-key", transport=httpx.MockTransport(handler), sleep=lambda _: pytest.fail("slept beyond budget")).decide("safe", QUESTIONS)
    assert len(calls) == 1


def test_duplicate_json_answer_fields_rejected():
    content = json.dumps(response()).replace('"noul": 0.8', '"noul": 0.8, "noul": 0.2').encode()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    with pytest.raises(ProviderSchemaError):
        TypeSafeProvider(api_key="test-key", transport=transport).decide("safe", QUESTIONS)


def test_deep_json_is_redacted_schema_failure():
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"[" * 2000 + b"]" * 2000)
    )
    with pytest.raises(ProviderSchemaError):
        TypeSafeProvider(api_key="test-key", transport=transport).decide(
            "safe", QUESTIONS
        )
