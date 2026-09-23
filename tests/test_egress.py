import json
import socket
from dataclasses import replace

import httpx
import pytest
from test_decision_service import Provider, graph

from rootweft.decide.models import DecisionPolicy, NoulQuestion, ProviderError, RemoteRequiredError
from rootweft.decide.providers import TypeSafeProvider
from rootweft.decide.service import adjudicate, preview_egress
from rootweft.models import AdjudicationLayer, Evidence, StructuralLayer


def test_offline_and_preview_open_zero_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline operation opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert adjudicate(graph(), None, DecisionPolicy()).structural.nodes
    assert preview_egress(graph(), DecisionPolicy()).question_count == 1


@pytest.mark.parametrize("field", ["name", "qualified_name", "id", "relation", "path"])
def test_all_outbound_graph_labels_checked_for_secrets(field):
    original = graph()
    secret = "sk-credentialValue12345"
    if field in {"name", "qualified_name", "id"}:
        node = replace(original.structural.nodes[0], **{field: secret})
        nodes = (node,) + original.structural.nodes[1:]
        original = replace(original, structural=StructuralLayer(nodes))
        if field == "id":
            candidate = replace(original.adjudication.candidates[0], source=secret)
            original = replace(original, adjudication=AdjudicationLayer((candidate,)))
    else:
        candidate = original.adjudication.candidates[0]
        candidate = replace(
            candidate,
            **(
                {"evidence": Evidence(secret + ".py", 1, 1)}
                if field == "path"
                else {field: secret}
            ),
        )
        original = replace(original, adjudication=AdjudicationLayer((candidate,)))
    provider = Provider()
    result = adjudicate(original, provider, DecisionPolicy())
    assert not provider.calls
    assert result.adjudication.diagnostics[-1].code == "remote_failure"
    with pytest.raises(ProviderError) as caught:
        preview_egress(original, DecisionPolicy())
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "config/credentials.json",
        "key.pem",
        "../private.py",
        "C:private.py",
        "node_modules/package/index.js",
        "vendor/lib.py",
        ".git/hooks/run.py",
        ".ENV.production",
    ],
)
def test_unsafe_evidence_paths_block_egress(path):
    original = graph()
    candidate = replace(
        original.adjudication.candidates[0], evidence=Evidence(path, 1, 1)
    )
    original = replace(original, adjudication=AdjudicationLayer((candidate,)))
    with pytest.raises(ProviderError):
        preview_egress(original, DecisionPolicy())


@pytest.mark.parametrize(
    "state,instructions", [("sk-secretvalue12345", "safe"), ("safe", "password=hidden")]
)
def test_provider_public_boundary_checks_state_and_questions(state, instructions):
    def forbidden(request):
        pytest.fail("secret reached transport")

    provider = TypeSafeProvider(
        api_key="test-key", transport=httpx.MockTransport(forbidden)
    )
    with pytest.raises(ProviderError):
        provider.decide(state, (NoulQuestion("q", instructions),))


@pytest.mark.parametrize("path", [
    "credentials/production.py", "secrets/customer.py",
    ".env.production/config.py", ".npmrc/config.py",
])
@pytest.mark.parametrize("location", ["candidate", "source", "option"])
@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("boundary", ["provider", "transport"])
def test_credential_parent_blocks_every_evidence_location(path, location, required, boundary):
    original = graph()
    evidence = Evidence(path, 1, 2)
    if location == "candidate":
        candidate = replace(original.adjudication.candidates[0], evidence=evidence)
        original = replace(original, adjudication=AdjudicationLayer((candidate,)))
    else:
        selected = "source" if location == "source" else "a"
        nodes = tuple(replace(node, evidence=evidence) if node.id == selected else node for node in original.structural.nodes)
        original = replace(original, structural=StructuralLayer(nodes))
    before = json.dumps(original.structural.to_dict(), sort_keys=True).encode()
    transport_calls = []

    def handler(request):
        transport_calls.append(request)
        return httpx.Response(500)

    provider = Provider() if boundary == "provider" else TypeSafeProvider(
        api_key="test-key", policy=DecisionPolicy(max_retries=0), transport=httpx.MockTransport(handler)
    )
    if required:
        with pytest.raises(RemoteRequiredError) as caught:
            adjudicate(original, provider, DecisionPolicy(), require_remote=True)
        result = caught.value.graph
    else:
        result = adjudicate(original, provider, DecisionPolicy())
    assert result.structural is original.structural
    assert json.dumps(result.structural.to_dict(), sort_keys=True).encode() == before
    assert transport_calls == []
    if boundary == "provider":
        assert provider.calls == []
    assert result.adjudication.diagnostics[-1].code == "remote_failure"
    assert result.adjudication.decisions == ()
    with pytest.raises(ProviderError):
        preview_egress(original, DecisionPolicy())
