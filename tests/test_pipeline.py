import hashlib
from pathlib import Path

import pytest

from rootweft.pipeline import BuildOptions, GraphLimits, build_structural_graph
from rootweft.scanner import ScanLimits
from rootweft.serialization import canonical_json

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_repo"


def test_sample_build_is_byte_stable_and_has_valid_endpoints() -> None:
    first = build_structural_graph(BuildOptions(SAMPLE, ScanLimits()))
    second = build_structural_graph(BuildOptions(SAMPLE, ScanLimits()))
    encoded = canonical_json(first)
    assert encoded == canonical_json(second)
    assert (
        hashlib.sha256(encoded).hexdigest()
        == "7b892ed0e3e88cffce31ca56fc26559e4a38105b3add9905df87b99fac541d84"
    )
    ids = {node.id for node in first.structural.nodes}
    assert all(
        edge.source in ids and edge.target in ids for edge in first.structural.edges
    )
    assert any(node.kind == "external" for node in first.structural.nodes)


def test_global_node_limit_fails_closed() -> None:
    with pytest.raises(ValueError, match="max_nodes"):
        build_structural_graph(
            BuildOptions(SAMPLE, ScanLimits(), graph_limits=GraphLimits(max_nodes=1))
        )


def test_relative_typescript_import_links_local_module(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text("import './util';\n", encoding="utf-8")
    (tmp_path / "util.ts").write_text("export function helper() {}\n", encoding="utf-8")
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    imports = [edge for edge in graph.structural.edges if edge.relation == "imports"]
    assert len(imports) == 1
    target = next(
        node for node in graph.structural.nodes if node.id == imports[0].target
    )
    assert target.kind == "module"
    assert target.evidence.path == "util.ts"
    assert not any(node.kind == "external" for node in graph.structural.nodes)


def test_dynamic_import_stays_unresolved_with_diagnostic(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("__import__('pkg')\n", encoding="utf-8")
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    assert not any(edge.relation == "imports" for edge in graph.structural.edges)
    assert not any(node.kind == "external" for node in graph.structural.nodes)
    assert any(
        diagnostic.code == "dynamic_reference"
        for diagnostic in graph.structural.diagnostics
    )
