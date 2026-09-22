"""Offline structural graph construction with bounded global output."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from rootweft.extract import extract_javascript, extract_markdown, extract_python
from rootweft.extract.base import ExtractionBatch, Reference
from rootweft.ids import stable_id
from rootweft.models import (
    SCHEMA_VERSION,
    AdjudicationLayer,
    Diagnostic,
    Edge,
    Evidence,
    GraphDocument,
    Node,
    StructuralLayer,
)
from rootweft.resolver import internal_import_targets, resolve_references
from rootweft.scanner import ScanLimits, ScannedFile, scan_repository


@dataclass(frozen=True)
class GraphLimits:
    max_nodes: int = 100_000
    max_edges: int = 200_000
    max_candidates: int = 20_000
    max_diagnostics: int = 1_000
    max_candidate_options: int = 16

    def __post_init__(self) -> None:
        for name in (
            "max_nodes",
            "max_edges",
            "max_candidates",
            "max_diagnostics",
            "max_candidate_options",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class BuildOptions:
    root: Path
    limits: ScanLimits
    include_markdown: bool = True
    graph_limits: GraphLimits = GraphLimits()


def build_structural_graph(options: BuildOptions) -> GraphDocument:
    """Scan and extract a repository without executing or importing its code."""
    scan = scan_repository(options.root, options.limits)
    budget = options.graph_limits
    nodes: list[Node] = []
    edges: list[Edge] = []
    refs: list[Reference] = []
    diagnostics: list[Diagnostic] = list(scan.diagnostics)
    _check("max_diagnostics", len(diagnostics), budget.max_diagnostics)
    markdown: list[ScannedFile] = []
    for file in scan.files:
        if file.language == "markdown":
            if options.include_markdown:
                markdown.append(file)
            continue
        batch: ExtractionBatch | None = None
        if file.language == "python":
            batch = extract_python(file)
        elif file.language in {"javascript", "typescript"}:
            batch = extract_javascript(file)
        if batch is not None:
            _append_batch(batch, nodes, edges, refs, diagnostics, budget)
    symbols = frozenset(
        node.name for node in nodes if node.kind in {"function", "class", "method"}
    )
    for file in markdown:
        _append_batch(
            extract_markdown(file, symbols), nodes, edges, refs, diagnostics, budget
        )
    for ref in refs:
        if ref.dynamic:
            diagnostics.append(
                Diagnostic(
                    code="dynamic_reference",
                    message="dynamic reference left unresolved",
                    evidence=ref.evidence,
                )
            )
            _check("max_diagnostics", len(diagnostics), budget.max_diagnostics)
    _add_external_nodes(nodes, refs, budget)
    resolved, candidates = resolve_references(
        nodes, refs, max_candidates=budget.max_candidate_options
    )
    edges.extend(resolved)
    _check("max_edges", len(edges), budget.max_edges)
    _check("max_candidates", len(candidates), budget.max_candidates)
    node_ids = {node.id for node in nodes}
    if len(node_ids) != len(nodes):
        raise ValueError("duplicate graph node id")
    edge_ids = {edge.id for edge in edges}
    if len(edge_ids) != len(edges):
        raise ValueError("duplicate graph edge id")
    if any(
        edge.source not in node_ids or edge.target not in node_ids for edge in edges
    ):
        raise ValueError("graph edge endpoint is missing")
    return GraphDocument(
        schema_version=SCHEMA_VERSION,
        extractor_version="rootweft.extract.v1",
        decision_policy_version="rootweft.policy.v1",
        structural=StructuralLayer(
            nodes=tuple(sorted(nodes, key=lambda node: node.id)),
            edges=tuple(sorted(edges, key=lambda edge: edge.id)),
            diagnostics=tuple(sorted(diagnostics, key=_diagnostic_key)),
        ),
        adjudication=AdjudicationLayer(candidates=candidates),
    )


def _append_batch(
    batch: ExtractionBatch,
    nodes: list[Node],
    edges: list[Edge],
    refs: list[Reference],
    diagnostics: list[Diagnostic],
    budget: GraphLimits,
) -> None:
    nodes.extend(batch.nodes)
    edges.extend(batch.edges)
    refs.extend(batch.references)
    diagnostics.extend(batch.diagnostics)
    _check("max_nodes", len(nodes), budget.max_nodes)
    _check("max_edges", len(edges), budget.max_edges)
    _check("max_candidates", len(refs), budget.max_candidates + len(nodes))
    _check("max_diagnostics", len(diagnostics), budget.max_diagnostics)


def _add_external_nodes(
    nodes: list[Node], refs: Iterable[Reference], budget: GraphLimits
) -> None:
    internal_nodes = tuple(nodes)
    sources = {node.id: node for node in nodes}
    external: set[tuple[str, str]] = set()
    for ref in refs:
        if ref.dynamic or ref.relation != "imports":
            continue
        source = sources[ref.source_id]
        language = source.language
        if ref.name.startswith((".", "../")):
            continue
        if not internal_import_targets(source, ref, internal_nodes):
            external.add((language, ref.name))
    for language, name in sorted(external):
        source_path = f"<external>/{language}"
        nodes.append(
            Node(
                id=stable_id("external", language, name),
                kind="external",
                name=name,
                qualified_name=name,
                language="external",
                evidence=Evidence(source_path, 1, 1),
                metadata={"import_language": language},
            )
        )
        _check("max_nodes", len(nodes), budget.max_nodes)


def _check(label: str, count: int, maximum: int) -> None:
    if count > maximum:
        raise ValueError(f"{label} exceeded: {count} > {maximum}")


def _diagnostic_key(item: Diagnostic) -> tuple[str, str, str, int]:
    evidence = item.evidence
    return (
        item.code,
        item.message,
        evidence.path if evidence else "",
        evidence.start_line if evidence else 0,
    )
