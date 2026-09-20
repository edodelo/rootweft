from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest

from rootweft.ids import evidence_fingerprint, stable_id
from rootweft.models import (
    AdjudicationLayer,
    Candidate,
    Decision,
    Diagnostic,
    Edge,
    Evidence,
    Node,
    StructuralLayer,
)


def test_node_is_immutable_and_serializes_relative_evidence() -> None:
    """Catches mutable graph data or an absolute source path entering an artifact."""
    node = Node(
        id="node-1",
        kind="function",
        name="run",
        qualified_name="module.run",
        language="python",
        evidence=Evidence(path="src/app.py", start_line=3, end_line=5),
    )

    assert node.to_dict()["evidence"] == {
        "path": "src/app.py",
        "start_line": 3,
        "end_line": 5,
    }
    with pytest.raises(FrozenInstanceError):
        node.name = "changed"  # type: ignore[misc]


def test_node_metadata_cannot_be_mutated() -> None:
    """Catches a frozen node exposing a mutable metadata mapping."""
    node = Node(
        id="node-1",
        kind="function",
        name="run",
        qualified_name="module.run",
        language="python",
        evidence=Evidence(path="src/app.py", start_line=3, end_line=5),
        metadata={"visibility": "public"},
    )

    with pytest.raises(TypeError):
        node.metadata["visibility"] = "private"  # type: ignore[index]


def test_nested_metadata_is_immutable_and_to_dict_returns_json_values() -> None:
    """Catches a frozen node leaking mutable nested JSON values."""
    node = Node(
        id="node-1",
        kind="function",
        name="run",
        qualified_name="module.run",
        language="python",
        evidence=Evidence(path="src/app.py", start_line=3, end_line=5),
        metadata={
            "labels": ["public"],
            "details": {"visibility": "public"},
        },
    )

    assert node.metadata["labels"] == ("public",)
    with pytest.raises(TypeError):
        node.metadata["details"]["visibility"] = "private"  # type: ignore[index]

    payload = node.to_dict()
    assert payload["metadata"] == {
        "labels": ["public"],
        "details": {"visibility": "public"},
    }
    payload["metadata"]["details"]["visibility"] = "private"
    assert node.metadata["details"]["visibility"] == "public"


def test_layer_collections_and_candidate_options_are_normalized_to_tuples() -> None:
    """Catches callers smuggling mutable lists into frozen graph collections."""
    evidence = Evidence(path="src/app.py", start_line=1, end_line=1)
    node = Node("node-1", "function", "run", "app.run", "python", evidence)
    edge = Edge("edge-1", "node-1", "node-1", "calls", "parser", "accepted", evidence)
    candidate = Candidate(
        "candidate-1",
        "node-1",
        "calls",
        evidence,
        ["node-1"],  # type: ignore[arg-type]
    )

    structural = StructuralLayer(  # type: ignore[arg-type]
        nodes=[node], edges=[edge], diagnostics=[]
    )
    adjudication = AdjudicationLayer(  # type: ignore[arg-type]
        candidates=[candidate], decisions=[], diagnostics=[]
    )

    assert candidate.options == ("node-1",)
    assert isinstance(structural.nodes, tuple)
    assert isinstance(adjudication.candidates, tuple)


def test_layers_reject_duplicate_identifiers() -> None:
    """Catches ambiguous graph layers with duplicate canonical identifiers."""
    evidence = Evidence(path="src/app.py", start_line=1, end_line=1)
    node = Node("node-1", "function", "run", "app.run", "python", evidence)
    edge = Edge("edge-1", "node-1", "node-1", "calls", "parser", "accepted", evidence)
    candidate = Candidate("candidate-1", "node-1", "calls", evidence, ("node-1",))
    decision = Decision("decision-1", "candidate-1", "review", {})

    with pytest.raises(ValueError, match="duplicate node id"):
        StructuralLayer(nodes=(node, node))
    with pytest.raises(ValueError, match="duplicate edge id"):
        StructuralLayer(edges=(edge, edge))
    with pytest.raises(ValueError, match="duplicate candidate id"):
        AdjudicationLayer(candidates=(candidate, candidate))
    with pytest.raises(ValueError, match="duplicate decision id"):
        AdjudicationLayer(decisions=(decision, decision))


def test_diagnostics_have_a_complete_canonical_order() -> None:
    """Catches equal partial sort keys preserving incidental diagnostic order."""
    early = Diagnostic("syntax", "invalid", Evidence("src/app.py", 2, 2))
    late = Diagnostic("syntax", "invalid", Evidence("src/app.py", 10, 10))

    assert (
        StructuralLayer(diagnostics=(late, early)).to_dict()
        == StructuralLayer(diagnostics=(early, late)).to_dict()
    )


def test_stable_identifiers_are_deterministic_and_unambiguous() -> None:
    """Catches ID concatenation that confuses different sequences of parts."""
    assert stable_id("alpha", "beta") == stable_id("alpha", "beta")
    assert stable_id("a", "bc") != stable_id("ab", "c")
    assert evidence_fingerprint("rootweft") == sha256(b"rootweft").hexdigest()


def test_evidence_rejects_absolute_paths() -> None:
    """Catches artifacts that would become machine-specific through source paths."""
    with pytest.raises(ValueError, match="relative POSIX"):
        Evidence(path="C:/repository/src/app.py", start_line=1, end_line=1)
