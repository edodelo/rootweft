"""Conservative, deterministic resolution of extracted structural references."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from collections.abc import Iterable
from pathlib import PurePosixPath

from rootweft.extract.base import Reference
from rootweft.ids import stable_id
from rootweft.models import Candidate, Edge, Node


def resolve_references(
    nodes: Iterable[Node], references: Iterable[Reference], max_candidates: int = 16
) -> tuple[tuple[Edge, ...], tuple[Candidate, ...]]:
    """Accept only exact bindings; leave uncertainty as bounded candidates."""
    if isinstance(max_candidates, bool) or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer")
    ordered_nodes = tuple(sorted(nodes, key=lambda node: node.id))
    by_id = {node.id: node for node in ordered_nodes}
    if len(by_id) != len(ordered_nodes):
        raise ValueError("duplicate node id")
    refs = sorted(
        references,
        key=lambda ref: (
            ref.source_id,
            ref.relation,
            ref.evidence.path,
            ref.evidence.start_line,
            ref.evidence.end_line,
            ref.name,
            ref.dynamic,
        ),
    )
    edges: list[Edge] = []
    candidates: list[Candidate] = []
    occurrences: dict[tuple[str, str, str, int, int, str], int] = defaultdict(int)
    for ref in refs:
        source = by_id.get(ref.source_id)
        if source is None:
            raise ValueError(f"reference source is not a graph node: {ref.source_id}")
        if ref.dynamic:
            continue
        matches = _matches(source, ref, ordered_nodes)
        if not matches:
            continue
        key = (
            ref.source_id,
            ref.relation,
            ref.evidence.path,
            ref.evidence.start_line,
            ref.evidence.end_line,
            ref.name,
        )
        occurrences[key] += 1
        evidence_key = stable_id(
            ref.evidence.path,
            str(ref.evidence.start_line),
            str(ref.evidence.end_line),
            ref.name,
            str(occurrences[key]),
        )
        if len(matches) == 1:
            target = matches[0]
            edges.append(
                Edge(
                    id=stable_id(
                        "resolver", ref.source_id, target.id, ref.relation, evidence_key
                    ),
                    source=ref.source_id,
                    target=target.id,
                    relation=ref.relation,
                    origin="resolver",
                    status="accepted",
                    evidence=ref.evidence,
                )
            )
        else:
            options = tuple(sorted(node.id for node in matches))[:max_candidates]
            candidates.append(
                Candidate(
                    id=stable_id(
                        "candidate", ref.source_id, ref.relation, evidence_key, *options
                    ),
                    source=ref.source_id,
                    relation=ref.relation,
                    evidence=ref.evidence,
                    options=options,
                )
            )
    return tuple(sorted(edges, key=lambda edge: edge.id)), tuple(
        sorted(candidates, key=lambda candidate: candidate.id)
    )


def _matches(source: Node, ref: Reference, nodes: tuple[Node, ...]) -> tuple[Node, ...]:
    if ref.relation == "imports":
        internal = internal_import_targets(source, ref, nodes)
        if internal:
            return internal
        return tuple(
            node
            for node in nodes
            if node.id != source.id
            and node.kind == "external"
            and node.metadata.get("import_language") == source.language
            and node.qualified_name == ref.name
        )
    if ref.relation not in {"calls", "mentions", "references"}:
        return ()
    if ref.relation == "calls" and ref.shadowed:
        return ()
    viable = tuple(
        node
        for node in nodes
        if node.kind in {"function", "method", "class"}
        and (ref.relation == "mentions" or node.language == source.language)
        and (node.name == ref.name or node.qualified_name == ref.name)
        and (
            node.id != source.id
            or (
                ref.relation == "calls"
                and source.language == "python"
                and source.kind == "function"
                and ref.name == source.name
            )
        )
    )
    if ref.relation == "mentions":
        return viable
    module_names = {
        node.qualified_name
        for node in nodes
        if node.kind == "module" and node.evidence.path == source.evidence.path
    }
    local = tuple(
        node
        for node in viable
        if node.evidence.path == source.evidence.path
        and (
            node.qualified_name in {f"{module}.{node.name}" for module in module_names}
            or (node.kind == "class" and node.qualified_name == node.name)
            or (ref.name == node.qualified_name and "." in ref.name)
        )
    )
    if local:
        return local
    return viable if len(viable) > 1 else ()


def internal_import_targets(
    source: Node, ref: Reference, nodes: Iterable[Node]
) -> tuple[Node, ...]:
    """Find import targets with the same rules used by external classification."""
    normalized = normalized_import_name(source, ref.name)
    local_nodes = tuple(node for node in nodes if node.language == source.language)
    if ref.import_kind == "module":
        return tuple(
            node
            for node in local_nodes
            if node.kind == "module"
            and node.qualified_name == normalized
            and node.id != source.id
        )
    if ref.import_kind != "symbol" or source.language != "python":
        return ()
    owner_name, separator, symbol_name = normalized.rpartition(".")
    if not separator or not owner_name or not symbol_name:
        return ()
    owner_paths = {
        node.evidence.path
        for node in local_nodes
        if node.kind == "module" and node.qualified_name == owner_name
    }
    symbols = tuple(
        node
        for node in local_nodes
        if node.evidence.path in owner_paths
        and node.name == symbol_name
        and node.kind in {"function", "class"}
        and node.qualified_name in {f"{owner_name}.{symbol_name}", symbol_name}
    )
    if symbols:
        return symbols
    return tuple(
        node
        for node in local_nodes
        if node.kind == "module" and node.qualified_name == normalized
    )


def normalized_import_name(source: Node, name: str) -> str:
    """Map explicit relative module spelling to extractor module names."""
    if source.language in {"javascript", "typescript"} and name.startswith(
        ("./", "../")
    ):
        path = posixpath.normpath(
            posixpath.join(posixpath.dirname(source.evidence.path), name)
        )
        if path == ".." or path.startswith("../"):
            return name
        suffix = PurePosixPath(path).suffix.casefold()
        if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
            path = path[: -len(suffix)]
        return path.replace("/", ".")
    if source.language == "python" and name.startswith("."):
        level = len(name) - len(name.lstrip("."))
        parts = list(PurePosixPath(source.evidence.path).parent.parts)
        if level > len(parts) + 1:
            return name
        parts = parts[: len(parts) - level + 1]
        suffix = name[level:]
        return ".".join((*parts, suffix)) if parts else suffix
    return name
