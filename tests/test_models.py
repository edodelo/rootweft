from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from rootweft.models import Evidence, Node


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


def test_evidence_rejects_absolute_paths() -> None:
    """Catches artifacts that would become machine-specific through source paths."""
    with pytest.raises(ValueError, match="relative POSIX"):
        Evidence(path="C:/repository/src/app.py", start_line=1, end_line=1)
