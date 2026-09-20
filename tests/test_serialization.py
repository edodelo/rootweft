from __future__ import annotations

from pathlib import Path

import pytest

from rootweft.errors import IncompatibleSchemaError
from rootweft.models import (
    AdjudicationLayer,
    Edge,
    Evidence,
    GraphDocument,
    Node,
    StructuralLayer,
)
from rootweft.serialization import canonical_json, dump_graph, load_graph


def minimal_graph(*, nodes_in_reverse_order: bool) -> GraphDocument:
    evidence = Evidence(path="src/app.py", start_line=1, end_line=1)
    alpha = Node(
        id="node-alpha",
        kind="function",
        name="alpha",
        qualified_name="app.alpha",
        language="python",
        evidence=evidence,
    )
    beta = Node(
        id="node-beta",
        kind="function",
        name="beta",
        qualified_name="app.beta",
        language="python",
        evidence=evidence,
    )
    edge = Edge(
        id="edge-beta-alpha",
        source="node-beta",
        target="node-alpha",
        relation="calls",
        origin="parser",
        status="accepted",
        evidence=evidence,
    )
    nodes = (beta, alpha) if nodes_in_reverse_order else (alpha, beta)
    return GraphDocument(
        schema_version="rootweft.graph.v1",
        extractor_version="0.1.0a1",
        decision_policy_version="review-only.v1",
        structural=StructuralLayer(nodes=nodes, edges=(edge,)),
        adjudication=AdjudicationLayer(),
    )


def test_graph_json_is_canonical() -> None:
    """Catches serialization that preserves incidental construction order."""
    first = canonical_json(minimal_graph(nodes_in_reverse_order=True))
    second = canonical_json(minimal_graph(nodes_in_reverse_order=False))

    assert first == second
    assert b'"schema_version":"rootweft.graph.v1"' in first


def test_dump_and_load_preserve_the_graph(tmp_path: Path) -> None:
    """Catches graph writes or loads that silently lose layer content."""
    path = tmp_path / "graph.json"
    graph = minimal_graph(nodes_in_reverse_order=True)

    dump_graph(graph, path)

    assert path.read_bytes() == canonical_json(graph)
    assert load_graph(path) == minimal_graph(nodes_in_reverse_order=False)


def test_rejects_newer_schema(tmp_path: Path) -> None:
    """Catches silently accepting a graph format this package cannot interpret."""
    path = tmp_path / "graph.json"
    path.write_text('{"schema_version":"rootweft.graph.v99"}', encoding="utf-8")

    with pytest.raises(IncompatibleSchemaError):
        load_graph(path)
