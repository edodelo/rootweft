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


@pytest.mark.parametrize(
    "source",
    [
        "def helper(): pass\ndef main(helper):\n    helper()\n",
        "def helper(): pass\ndef main():\n    def helper(): pass\n    helper()\n",
        "def helper(): pass\ndef main():\n    (lambda helper: helper())(object())\n",
        "def helper(): pass\ndef main(items):\n    [helper() for helper in items]\n",
        "def helper(): pass\nhelper = lambda: None\ndef main():\n    helper()\n",
    ],
)
def test_python_local_binding_does_not_call_global_helper(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    assert not any(edge.relation == "calls" for edge in graph.structural.edges)


def test_python_import_form_distinguishes_module_from_class(tmp_path: Path) -> None:
    (tmp_path / "lib.py").write_text("class Helper: pass\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from lib import Helper\nimport Helper\n", encoding="utf-8"
    )
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    imports = {
        edge.evidence.start_line: edge
        for edge in graph.structural.edges
        if edge.relation == "imports"
    }
    targets = {node.id: node for node in graph.structural.nodes}
    assert targets[imports[1].target].kind == "class"
    assert targets[imports[1].target].evidence.path == "lib.py"
    assert targets[imports[2].target].kind == "external"


def test_relative_dotted_typescript_import_keeps_dotted_basename(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.ts").write_text("import './util.test';\n", encoding="utf-8")
    (tmp_path / "util.ts").write_text("export function wrong() {}\n", encoding="utf-8")
    (tmp_path / "util.test.ts").write_text(
        "export function right() {}\n", encoding="utf-8"
    )
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    imports = [edge for edge in graph.structural.edges if edge.relation == "imports"]
    targets = {node.id: node for node in graph.structural.nodes}
    assert len(imports) == 1
    assert targets[imports[0].target].evidence.path == "util.test.ts"


def test_recursive_python_call_targets_itself(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def helper():\n    helper()\n", encoding="utf-8")
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    calls = [edge for edge in graph.structural.edges if edge.relation == "calls"]
    assert len(calls) == 1
    assert calls[0].source == calls[0].target


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("app.js", "function helper(helper) { helper(); }\n"),
        ("app.ts", "function helper(helper: () => void) { helper(); }\n"),
    ],
)
def test_javascript_self_name_with_parameter_is_not_proven_recursive(
    tmp_path: Path, filename: str, source: str
) -> None:
    (tmp_path / filename).write_text(source, encoding="utf-8")
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    assert not any(edge.relation == "calls" for edge in graph.structural.edges)


def test_javascript_self_name_without_binding_proof_stays_unresolved(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.js").write_text(
        "function helper() { helper(); }\n", encoding="utf-8"
    )
    graph = build_structural_graph(BuildOptions(tmp_path, ScanLimits()))
    assert not any(edge.relation == "calls" for edge in graph.structural.edges)


def test_edge_and_diagnostic_limits_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(
        "def helper():\n    __import__('one')\n    __import__('two')\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="max_edges"):
        build_structural_graph(
            BuildOptions(tmp_path, ScanLimits(), graph_limits=GraphLimits(max_edges=1))
        )
    with pytest.raises(ValueError, match="max_diagnostics"):
        build_structural_graph(
            BuildOptions(
                tmp_path, ScanLimits(), graph_limits=GraphLimits(max_diagnostics=1)
            )
        )


def test_candidate_limit_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def helper(): pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def helper(): pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("helper\nhelper\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_candidates"):
        build_structural_graph(
            BuildOptions(
                tmp_path, ScanLimits(), graph_limits=GraphLimits(max_candidates=1)
            )
        )


@pytest.mark.parametrize(
    "limits",
    [ScanLimits(max_files=1), ScanLimits(max_total_bytes=8)],
    ids=["max_files", "max_total_bytes"],
)
def test_global_scan_limits_fail_closed(tmp_path: Path, limits: ScanLimits) -> None:
    """Catches a truncated or empty scan being published as a complete graph."""
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("def f():\n    return 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeded"):
        build_structural_graph(BuildOptions(tmp_path, limits))


def test_oversized_single_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "small.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("x = 1\n" * 100, encoding="utf-8")
    document = build_structural_graph(
        BuildOptions(tmp_path, ScanLimits(max_file_bytes=100))
    )
    assert "max_file_bytes" in [d.code for d in document.structural.diagnostics]
    assert any(node.name == "f" for node in document.structural.nodes)
