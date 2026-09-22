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
    assert hashlib.sha256(encoded).hexdigest() == "GOLDEN_HASH"
    ids = {node.id for node in first.structural.nodes}
    assert all(edge.source in ids and edge.target in ids for edge in first.structural.edges)
    assert any(node.kind == "external" for node in first.structural.nodes)


def test_global_node_limit_fails_closed() -> None:
    with pytest.raises(ValueError, match="max_nodes"):
        build_structural_graph(BuildOptions(SAMPLE, ScanLimits(), graph_limits=GraphLimits(max_nodes=1)))
