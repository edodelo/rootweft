"""Immutable, JSON-compatible graph data transfer objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Self

SCHEMA_VERSION = "rootweft.graph.v1"


def _relative_posix_path(path: str) -> str:
    if (
        not path
        or "\\" in path
        or PurePosixPath(path).is_absolute()
        or PureWindowsPath(path).is_absolute()
    ):
        msg = "path must be a relative POSIX path"
        raise ValueError(msg)
    return path


def _model_id(value: str, *, field_name: str) -> str:
    if not value:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        msg = f"{field_name} must not contain an absolute path"
        raise ValueError(msg)
    return value


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class Evidence:
    path: str
    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        _relative_posix_path(self.path)
        if self.start_line < 1 or self.end_line < self.start_line:
            msg = "evidence lines must be one-based and ordered"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            path=_required_str(value, "path"),
            start_line=_required_int(value, "start_line"),
            end_line=_required_int(value, "end_line"),
        )


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    name: str
    qualified_name: str | None
    language: str
    evidence: Evidence
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _model_id(self.id, field_name="node id")
        object.__setattr__(self, "metadata", _mapping(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "language": self.language,
            "evidence": self.evidence.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            id=_required_str(value, "id"),
            kind=_required_str(value, "kind"),
            name=_required_str(value, "name"),
            qualified_name=_optional_str(value, "qualified_name"),
            language=_required_str(value, "language"),
            evidence=Evidence.from_dict(_required_mapping(value, "evidence")),
            metadata=_optional_mapping(value, "metadata"),
        )


@dataclass(frozen=True)
class Edge:
    id: str
    source: str
    target: str
    relation: str
    origin: str
    status: str
    evidence: Evidence
    decision_provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("edge id", self.id),
            ("source", self.source),
            ("target", self.target),
        ):
            _model_id(value, field_name=field_name)
        if self.decision_provenance is not None:
            object.__setattr__(
                self, "decision_provenance", _mapping(self.decision_provenance)
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "origin": self.origin,
            "status": self.status,
            "evidence": self.evidence.to_dict(),
        }
        if self.decision_provenance is not None:
            result["decision_provenance"] = dict(self.decision_provenance)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        provenance = value.get("decision_provenance")
        if provenance is not None and not isinstance(provenance, Mapping):
            raise ValueError("decision_provenance must be an object")
        return cls(
            id=_required_str(value, "id"),
            source=_required_str(value, "source"),
            target=_required_str(value, "target"),
            relation=_required_str(value, "relation"),
            origin=_required_str(value, "origin"),
            status=_required_str(value, "status"),
            evidence=Evidence.from_dict(_required_mapping(value, "evidence")),
            decision_provenance=provenance,
        )


@dataclass(frozen=True)
class Candidate:
    id: str
    source: str
    relation: str
    evidence: Evidence
    options: tuple[str, ...]

    def __post_init__(self) -> None:
        _model_id(self.id, field_name="candidate id")
        _model_id(self.source, field_name="source")
        for option in self.options:
            _model_id(option, field_name="candidate option")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "relation": self.relation,
            "evidence": self.evidence.to_dict(),
            "options": list(sorted(self.options)),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        options = value.get("options")
        if not isinstance(options, list) or not all(
            isinstance(option, str) for option in options
        ):
            raise ValueError("options must be an array of strings")
        return cls(
            id=_required_str(value, "id"),
            source=_required_str(value, "source"),
            relation=_required_str(value, "relation"),
            evidence=Evidence.from_dict(_required_mapping(value, "evidence")),
            options=tuple(options),
        )


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    evidence: Evidence | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.evidence is not None:
            result["evidence"] = self.evidence.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        evidence = value.get("evidence")
        if evidence is not None and not isinstance(evidence, Mapping):
            raise ValueError("diagnostic evidence must be an object")
        return cls(
            code=_required_str(value, "code"),
            message=_required_str(value, "message"),
            evidence=Evidence.from_dict(evidence) if evidence is not None else None,
        )


@dataclass(frozen=True)
class Decision:
    id: str
    candidate_id: str
    state: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        _model_id(self.id, field_name="decision id")
        _model_id(self.candidate_id, field_name="candidate id")
        object.__setattr__(self, "provenance", _mapping(self.provenance))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "candidate_id": self.candidate_id,
            "state": self.state,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            id=_required_str(value, "id"),
            candidate_id=_required_str(value, "candidate_id"),
            state=_required_str(value, "state"),
            provenance=_required_mapping(value, "provenance"),
        )


@dataclass(frozen=True)
class StructuralLayer:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                node.to_dict() for node in sorted(self.nodes, key=lambda node: node.id)
            ],
            "edges": [
                edge.to_dict() for edge in sorted(self.edges, key=lambda edge: edge.id)
            ],
            "diagnostics": [
                diagnostic.to_dict()
                for diagnostic in sorted(
                    self.diagnostics,
                    key=lambda diagnostic: (
                        diagnostic.code,
                        diagnostic.evidence.path if diagnostic.evidence else "",
                        diagnostic.message,
                    ),
                )
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            nodes=tuple(
                Node.from_dict(item)
                for item in _required_list_of_mappings(value, "nodes")
            ),
            edges=tuple(
                Edge.from_dict(item)
                for item in _required_list_of_mappings(value, "edges")
            ),
            diagnostics=tuple(
                Diagnostic.from_dict(item)
                for item in _optional_list_of_mappings(value, "diagnostics")
            ),
        )


@dataclass(frozen=True)
class AdjudicationLayer:
    candidates: tuple[Candidate, ...] = ()
    decisions: tuple[Decision, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [
                candidate.to_dict()
                for candidate in sorted(
                    self.candidates, key=lambda candidate: candidate.id
                )
            ],
            "decisions": [
                decision.to_dict()
                for decision in sorted(self.decisions, key=lambda decision: decision.id)
            ],
            "diagnostics": [
                diagnostic.to_dict()
                for diagnostic in sorted(
                    self.diagnostics,
                    key=lambda diagnostic: (
                        diagnostic.code,
                        diagnostic.evidence.path if diagnostic.evidence else "",
                        diagnostic.message,
                    ),
                )
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            candidates=tuple(
                Candidate.from_dict(item)
                for item in _optional_list_of_mappings(value, "candidates")
            ),
            decisions=tuple(
                Decision.from_dict(item)
                for item in _optional_list_of_mappings(value, "decisions")
            ),
            diagnostics=tuple(
                Diagnostic.from_dict(item)
                for item in _optional_list_of_mappings(value, "diagnostics")
            ),
        )


@dataclass(frozen=True)
class GraphDocument:
    schema_version: str
    extractor_version: str
    decision_policy_version: str
    structural: StructuralLayer
    adjudication: AdjudicationLayer

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "extractor_version": self.extractor_version,
            "decision_policy_version": self.decision_policy_version,
            "structural": self.structural.to_dict(),
            "adjudication": self.adjudication.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        return cls(
            schema_version=_required_str(value, "schema_version"),
            extractor_version=_required_str(value, "extractor_version"),
            decision_policy_version=_required_str(value, "decision_policy_version"),
            structural=StructuralLayer.from_dict(
                _required_mapping(value, "structural")
            ),
            adjudication=AdjudicationLayer.from_dict(
                _required_mapping(value, "adjudication")
            ),
        )


def _required_str(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _optional_str(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ValueError(f"{key} must be a string or null")
    return item


def _required_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"{key} must be an integer")
    return item


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{key} must be an object")
    return item


def _optional_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key, {})
    if not isinstance(item, Mapping):
        raise ValueError(f"{key} must be an object")
    return item


def _required_list_of_mappings(
    value: Mapping[str, Any], key: str
) -> list[Mapping[str, Any]]:
    item = value.get(key)
    if not isinstance(item, list) or not all(
        isinstance(entry, Mapping) for entry in item
    ):
        raise ValueError(f"{key} must be an array of objects")
    return item


def _optional_list_of_mappings(
    value: Mapping[str, Any], key: str
) -> list[Mapping[str, Any]]:
    item = value.get(key, [])
    if not isinstance(item, list) or not all(
        isinstance(entry, Mapping) for entry in item
    ):
        raise ValueError(f"{key} must be an array of objects")
    return item
