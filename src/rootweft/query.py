"""Bounded, deterministic read-only queries over structural graph documents."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from rootweft.errors import EdgeNotFoundError, NodeNotFoundError
from rootweft.models import Edge, GraphDocument, Node

MAX_QUERY_RESULTS = 100
MAX_PATH_DEPTH = 32
MAX_PATH_VISITS = 10_000


@dataclass(frozen=True)
class GraphIndex:
    document: GraphDocument
    _nodes: dict[str, Node] = field(repr=False)
    _edges: dict[str, Edge] = field(repr=False)
    _outgoing: dict[str, tuple[Edge, ...]] = field(repr=False)
    _incoming: dict[str, tuple[Edge, ...]] = field(repr=False)

    @classmethod
    def from_document(cls, document: GraphDocument) -> GraphIndex:
        nodes = {node.id: node for node in document.structural.nodes}
        edges = {edge.id: edge for edge in document.structural.edges}
        if len(nodes) != len(document.structural.nodes) or len(edges) != len(
            document.structural.edges
        ):
            raise ValueError("duplicate graph identity")
        outgoing: dict[str, list[Edge]] = defaultdict(list)
        incoming: dict[str, list[Edge]] = defaultdict(list)
        for edge in document.structural.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError("edge endpoint is missing")
            outgoing[edge.source].append(edge)
            incoming[edge.target].append(edge)
        return cls(
            document,
            nodes,
            edges,
            {
                key: tuple(sorted(value, key=lambda edge: edge.id))
                for key, value in outgoing.items()
            },
            {
                key: tuple(sorted(value, key=lambda edge: edge.id))
                for key, value in incoming.items()
            },
        )

    def search_nodes(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        kind: str | None = None,
    ) -> dict[str, Any]:
        _page(limit, offset)
        needle = query.casefold()
        matches = [
            node
            for node in sorted(self._nodes.values(), key=lambda node: node.id)
            if (kind is None or node.kind == kind)
            and (
                needle in node.name.casefold()
                or needle in (node.qualified_name or "").casefold()
            )
        ]
        return {
            "layer": "structural",
            "nodes": [node.to_dict() for node in matches[offset : offset + limit]],
            "total": len(matches),
            "limit": limit,
            "offset": offset,
        }

    def get_node(self, node_id: str) -> dict[str, Any]:
        node = self._require_node(node_id)
        return {"layer": "structural", "node": node.to_dict()}

    def neighbors(
        self,
        node_id: str,
        *,
        relation: str | None = None,
        direction: str = "both",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        self._require_node(node_id)
        _page(limit, offset)
        if direction not in {"in", "out", "both"}:
            raise ValueError("direction must be in, out, or both")
        edges = (
            (*self._outgoing.get(node_id, ()), *self._incoming.get(node_id, ()))
            if direction == "both"
            else self._outgoing.get(node_id, ())
            if direction == "out"
            else self._incoming.get(node_id, ())
        )
        selected = sorted(
            {
                edge.id: edge
                for edge in edges
                if relation is None or edge.relation == relation
            }.values(),
            key=lambda edge: edge.id,
        )
        return {
            "layer": "structural",
            "node_id": node_id,
            "edges": [edge.to_dict() for edge in selected[offset : offset + limit]],
            "total": len(selected),
            "limit": limit,
            "offset": offset,
        }

    def shortest_path(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int = 8,
        relation: str | None = None,
    ) -> dict[str, Any]:
        self._require_node(source_id)
        self._require_node(target_id)
        if (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or not 0 <= max_depth <= MAX_PATH_DEPTH
        ):
            raise ValueError(f"max_depth must be between 0 and {MAX_PATH_DEPTH}")
        pending: deque[tuple[str, tuple[Edge, ...]]] = deque([(source_id, ())])
        seen = {source_id}
        while pending:
            current, path = pending.popleft()
            if current == target_id:
                return {
                    "layer": "structural",
                    "source": source_id,
                    "target": target_id,
                    "edges": [edge.to_dict() for edge in path],
                    "found": True,
                }
            if len(path) == max_depth:
                continue
            for edge in self._outgoing.get(current, ()):
                if (
                    relation is not None and edge.relation != relation
                ) or edge.target in seen:
                    continue
                seen.add(edge.target)
                if len(seen) > MAX_PATH_VISITS:
                    raise ValueError("shortest_path visit limit exceeded")
                pending.append((edge.target, (*path, edge)))
        return {
            "layer": "structural",
            "source": source_id,
            "target": target_id,
            "edges": [],
            "found": False,
        }

    def explain_edge(self, edge_id: str) -> dict[str, Any]:
        edge = self._edges.get(edge_id)
        if edge is None:
            raise EdgeNotFoundError(f"edge not found: {edge_id}")
        return {
            "layer": "structural",
            "edge": edge.to_dict(),
            "source": self._nodes[edge.source].to_dict(),
            "target": self._nodes[edge.target].to_dict(),
        }

    def graph_stats(self) -> dict[str, Any]:
        kinds: dict[str, int] = defaultdict(int)
        relations: dict[str, int] = defaultdict(int)
        for node in self._nodes.values():
            kinds[node.kind] += 1
        for edge in self._edges.values():
            relations[edge.relation] += 1
        return {
            "layer": "structural",
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "candidate_count": len(self.document.adjudication.candidates),
            "diagnostic_count": len(self.document.structural.diagnostics),
            "nodes_by_kind": dict(sorted(kinds.items())),
            "edges_by_relation": dict(sorted(relations.items())),
        }

    def _require_node(self, node_id: str) -> Node:
        node = self._nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(f"node not found: {node_id}")
        return node


def _page(limit: int, offset: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_QUERY_RESULTS
    ):
        raise ValueError(f"limit must be between 1 and {MAX_QUERY_RESULTS}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
