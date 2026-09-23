import socket
from dataclasses import replace

import httpx
import pytest
from test_decision_service import Provider, graph

from rootweft.decide.models import DecisionPolicy, NoulQuestion, ProviderError
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
    [".env", "config/credentials.json", "key.pem", "../private.py", "C:private.py"],
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
