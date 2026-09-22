from pathlib import Path

import pytest

from rootweft.errors import EdgeNotFoundError, NodeNotFoundError
from rootweft.pipeline import BuildOptions, build_structural_graph
from rootweft.query import GraphIndex
from rootweft.scanner import ScanLimits

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_repo"


def test_queries_identify_layer_and_are_bounded() -> None:
    index = GraphIndex.from_document(
        build_structural_graph(BuildOptions(SAMPLE, ScanLimits()))
    )
    found = index.search_nodes("HELPER", limit=2)
    assert found["layer"] == "structural"
    assert 0 < len(found["nodes"]) <= 2
    source = next(
        node
        for node in index.document.structural.nodes
        if node.kind == "function" and node.name == "main"
    )
    neighbors = index.neighbors(source.id, limit=2)
    assert neighbors["layer"] == "structural"
    assert len(neighbors["edges"]) <= 2
    target = next(
        edge.target
        for edge in index.document.structural.edges
        if edge.source == source.id and edge.relation == "calls"
    )
    path = index.shortest_path(source.id, target, max_depth=3)
    assert path["layer"] == "structural"
    assert len(path["edges"]) == 1
    explained = index.explain_edge(path["edges"][0]["id"])
    assert explained["layer"] == "structural"
    assert index.graph_stats()["layer"] == "structural"


def test_missing_ids_differ_from_empty_results() -> None:
    index = GraphIndex.from_document(
        build_structural_graph(BuildOptions(SAMPLE, ScanLimits()))
    )
    assert index.search_nodes("nonexistent-pattern")["nodes"] == []
    with pytest.raises(NodeNotFoundError):
        index.neighbors("missing")
    with pytest.raises(EdgeNotFoundError):
        index.explain_edge("missing")


def test_recursive_cycle_and_pagination_are_bounded(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def helper():\n    helper()\n", encoding="utf-8")
    index = GraphIndex.from_document(
        build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    )
    helper = next(
        node for node in index.document.structural.nodes if node.name == "helper"
    )
    assert index.shortest_path(helper.id, helper.id, max_depth=0)["edges"] == []
    assert index.neighbors(helper.id, relation="calls")["total"] == 1
    assert (
        index.search_nodes("", limit=1, offset=1)["nodes"]
        != index.search_nodes("", limit=1, offset=0)["nodes"]
    )
    with pytest.raises(ValueError, match="max_depth"):
        index.shortest_path(helper.id, helper.id, max_depth=33)
    with pytest.raises(ValueError, match="limit"):
        index.neighbors(helper.id, limit=101)
