"""Shared extraction result types."""

from __future__ import annotations

from dataclasses import dataclass

from rootweft.models import Diagnostic, Edge, Evidence, Node


@dataclass(frozen=True)
class Reference:
    """An unresolved relationship emitted by a language extractor."""

    source_id: str
    name: str
    relation: str
    evidence: Evidence
    dynamic: bool = False


@dataclass(frozen=True)
class ExtractionBatch:
    """The structural output for one scanned source file."""

    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    references: tuple[Reference, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "references", tuple(self.references))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
