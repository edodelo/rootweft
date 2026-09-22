from pathlib import Path

import pytest

from rootweft.errors import EdgeNotFoundError, NodeNotFoundError
from rootweft.pipeline import BuildOptions, build_structural_graph
from rootweft.query import GraphIndex
from rootweft.scanner import ScanLimits


SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_repo"


def test_queries_identify_layer_and_are_bounded() -> None:
    index = GraphIndex.from_document(build_structural_graph(BuildOptions(SAMPLE, ScanLimits())))
    found = index.search_nodes("HELPER", limit=2)
    assert found["layer"] == "structural"
    assert 0 < len(found["nodes"]) <= 2
    source = next(node for node in index.document.structural.nodes if node.kind == "module" and node.name == "app")
    neighbors = index.neighbors(source.id, limit=2)
    assert neighbors["layer"] == "structural"
    assert len(neighbors["edges"]) <= 2
    target = next(edge.target for edge in index.document.structural.edges if edge.source == source.id and edge.relation == "calls")
    path = index.shortest_path(source.id, target, max_depth=3)
    assert path["layer"] == "structural"
    assert len(path["edges"]) == 1
    explained = index.explain_edge(path["edges"][0]["id"])
    assert explained["layer"] == "structural"
    assert index.graph_stats()["layer"] == "structural"


def test_missing_ids_differ_from_empty_results() -> None:
    index = GraphIndex.from_document(build_structural_graph(BuildOptions(SAMPLE, ScanLimits())))
    assert index.search_nodes("nonexistent-pattern")["nodes"] == []
    with pytest.raises(NodeNotFoundError):
        index.neighbors("missing")
    with pytest.raises(EdgeNotFoundError):
        index.explain_edge("missing")
