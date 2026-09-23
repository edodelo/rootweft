import json
import re
from dataclasses import replace
from datetime import datetime

import pytest

from rootweft.decide.models import (
    ChoiceQuestion,
    DecisionPolicy,
    NoulQuestion,
    ProviderAnswer,
    ProviderError,
    RemoteRequiredError,
    ScoreQuestion,
)
from rootweft.decide.service import adjudicate, preview_egress
from rootweft.models import (
    SCHEMA_VERSION,
    AdjudicationLayer,
    Candidate,
    Evidence,
    GraphDocument,
    Node,
    StructuralLayer,
)
from rootweft.serialization import canonical_json


def graph():
    evidence = Evidence("src/app.py", 1, 2)
    nodes = tuple(
        Node(key, "function", key, key, "python", evidence)
        for key in ("source", "a", "b")
    )
    return GraphDocument(
        SCHEMA_VERSION,
        "extract.v1",
        "offline.v1",
        StructuralLayer(nodes),
        AdjudicationLayer(
            (Candidate("candidate", "source", "calls", evidence, ("a", "b")),)
        ),
    )


class Provider:
    name = "typesafe"
    model = "jev-1.13.0"
    reproducible = True

    def __init__(self, failure=False):
        self.calls = []
        self.failure = failure

    def decide(self, state, questions):
        self.calls.append((state, questions))
        if self.failure:
            raise RuntimeError("sk-privatecredential123 raw-state")
        return tuple(
            ProviderAnswer.choice(
                q.id, "a", {"a": 0.9, "b": 0.1, "none": 0}, 0.8, self.model
            )
            for q in questions
        )


def test_success_is_review_only_and_structural_bytes_identical():
    original, provider = graph(), Provider()
    before = json.dumps(original.structural.to_dict(), sort_keys=True).encode()
    result = adjudicate(original, provider, DecisionPolicy())
    assert result.structural is original.structural
    assert json.dumps(result.structural.to_dict(), sort_keys=True).encode() == before
    (decision,) = result.adjudication.decisions
    assert decision.state == "review"
    assert decision.provenance["proposed_outcome"] == "a"
    assert decision.provenance["probabilities"] == {"a": 0.9, "b": 0.1, "none": 0}
    state, questions = provider.calls[0]
    assert isinstance(state, str) and isinstance(questions, tuple)
    assert isinstance(questions[0], ChoiceQuestion)
    decoded = json.loads(state)
    assert decoded["candidates"][0]["evidence"] == {
        "path": "src/app.py",
        "start_line": 1,
        "end_line": 2,
    }
    assert state == json.dumps(
        decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert state.encode() not in canonical_json(result)
    assert original.adjudication.decisions == ()


@pytest.mark.parametrize("required", [False, True])
def test_remote_failure_preserves_graph_and_redacts_error(required):
    original = graph()
    if required:
        with pytest.raises(RemoteRequiredError) as caught:
            adjudicate(original, Provider(True), DecisionPolicy(), require_remote=True)
        result = caught.value.graph
    else:
        result = adjudicate(original, Provider(True), DecisionPolicy())
    assert result.structural is original.structural
    assert result.adjudication.diagnostics[-1].code == "remote_failure"
    assert b"privatecredential" not in canonical_json(result)
    assert b"raw-state" not in canonical_json(result)


def test_no_provider_is_offline_and_required_failure_is_typed():
    original = graph()
    assert adjudicate(original, None, DecisionPolicy()) is original
    with pytest.raises(RemoteRequiredError) as caught:
        adjudicate(original, None, DecisionPolicy(), require_remote=True)
    assert caught.value.graph.structural is original.structural


def test_preview_reports_only_counts_relative_paths_and_bytes():
    preview = preview_egress(graph(), DecisionPolicy())
    assert preview.question_count == 1
    assert preview.candidate_count == 1
    assert preview.paths == ("src/app.py",)
    assert preview.estimated_bytes > 100
    assert set(preview.to_dict()) == {
        "question_count",
        "candidate_count",
        "paths",
        "estimated_bytes",
    }


def test_service_budgets_apply_even_to_custom_provider():
    provider = Provider()
    result = adjudicate(graph(), provider, DecisionPolicy(max_payload_bytes=1))
    assert not provider.calls
    assert result.adjudication.diagnostics[-1].code == "remote_failure"


def test_service_validates_custom_provider_answer_bijection():
    class Bad(Provider):
        def decide(self, state, questions):
            return ()

    result = adjudicate(graph(), Bad(), DecisionPolicy())
    assert not result.adjudication.decisions
    assert result.adjudication.diagnostics[-1].code == "remote_failure"


@pytest.mark.parametrize(
    "relation,expected", [("calls", NoulQuestion), ("mentions", ScoreQuestion)]
)
def test_single_option_candidates_get_atomic_question(relation, expected):
    class Atomic(Provider):
        def decide(self, state, questions):
            assert isinstance(questions[0], expected)
            raise ProviderError("stop")

    original = graph()
    candidate = replace(
        original.adjudication.candidates[0], options=("a",), relation=relation
    )
    original = replace(original, adjudication=AdjudicationLayer((candidate,)))
    adjudicate(original, Atomic(), DecisionPolicy())


def test_openrouter_provenance_records_request_and_returned_snapshot():
    class Routed(Provider):
        name = "openrouter-experimental"
        model = "typesafe/jev-1.13"

        def decide(self, state, questions):
            return (ProviderAnswer.choice(
                "candidate", "a", {"a": 1, "b": 0, "none": 0}, 1,
                "typesafe/jev-1.13-20260917",
            ),)

    result = adjudicate(graph(), Routed(), DecisionPolicy())
    provenance = result.adjudication.decisions[0].provenance
    assert provenance["requested_model"] == "typesafe/jev-1.13"
    assert provenance["model"] == "typesafe/jev-1.13-20260917"
    assert provenance["options"] == ("a", "b", "none")
    assert re.fullmatch("[0-9a-f]{64}", provenance["question_hash"])
    assert datetime.fromisoformat(provenance["timestamp"]).utcoffset().total_seconds() == 0
    assert provenance["reproducible"] is False


def test_question_hash_covers_state_changes_without_storing_metadata():
    original = graph()
    first = adjudicate(original, Provider(), DecisionPolicy())
    node = replace(original.structural.nodes[0], name="renamed", metadata={"source": "raw code"})
    changed = replace(original, structural=StructuralLayer((node,) + original.structural.nodes[1:]))
    provider = Provider()
    second = adjudicate(changed, provider, DecisionPolicy())
    assert first.adjudication.decisions[0].provenance["question_hash"] != second.adjudication.decisions[0].provenance["question_hash"]
    assert "raw code" not in provider.calls[0][0]
