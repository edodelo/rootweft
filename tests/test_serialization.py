from __future__ import annotations

import json
from pathlib import Path

import pytest

from rootweft.errors import CorruptGraphError, IncompatibleSchemaError
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


def test_rejects_corrupt_json(tmp_path: Path) -> None:
    """Catches malformed graph payloads being accepted or leaking decoder errors."""
    path = tmp_path / "graph.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(CorruptGraphError):
        load_graph(path)


def test_failed_replace_removes_the_temporary_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches failed atomic replacement leaving a partial sibling artifact behind."""
    path = tmp_path / "graph.json"

    def fail_replace(source: str, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("rootweft.serialization.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        dump_graph(minimal_graph(nodes_in_reverse_order=False), path)

    assert list(tmp_path.glob(".graph.json.*.tmp")) == []


def test_import_rejects_oversized_file_before_json_decoder(tmp_path, monkeypatch):
    path = tmp_path / "oversized.json"
    with path.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)

    def forbidden(*args, **kwargs):
        pytest.fail("oversized input reached JSON decoder")

    monkeypatch.setattr("rootweft.serialization.json.loads", forbidden)
    with pytest.raises(CorruptGraphError):
        load_graph(path)


@pytest.mark.parametrize("kind", ["options", "records", "depth"])
def test_import_preflight_rejects_excessive_structure(tmp_path, kind):
    raw = minimal_graph(nodes_in_reverse_order=False).to_dict()
    if kind == "options":
        raw["adjudication"]["candidates"] = [
            {
                "id": "candidate",
                "source": "node-alpha",
                "relation": "calls",
                "evidence": {"path": "app.py", "start_line": 1, "end_line": 1},
                "options": ["node-beta"] * 10000,
            }
        ]
    elif kind == "records":
        raw["structural"]["diagnostics"] = [{"code": "x", "message": "x"}] * 1001
    else:
        nested = {}
        for _ in range(50):
            nested = {"nested": nested}
        raw["structural"]["nodes"][0]["metadata"] = nested
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(CorruptGraphError):
        load_graph(path)
