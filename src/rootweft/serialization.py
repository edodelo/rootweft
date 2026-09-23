"""Canonical and atomic persistence for Rootweft graph documents."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from rootweft.errors import CorruptGraphError, IncompatibleSchemaError
from rootweft.models import SCHEMA_VERSION, GraphDocument

# Import/viewer budgets are independent of caller-controlled build settings.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_TEXT_LENGTH = 100_000
MAX_VALUE_DEPTH = 32
MAX_VALUE_ITEMS = 5_000_000
MAX_TOTAL_OPTIONS = 320_000


def _field(value: Any, name: str, default: Any = None) -> Any:
    return (
        value.get(name, default)
        if isinstance(value, Mapping)
        else getattr(value, name, default)
    )


def validate_graph_limits(document: Any) -> None:
    """Preflight raw or model graph without copying records or materializing JSON."""
    structural = _field(document, "structural", {})
    adjudication = _field(document, "adjudication", {})
    for layer, name, limit in (
        (structural, "nodes", 100_000),
        (structural, "edges", 200_000),
        (structural, "diagnostics", 1_000),
        (adjudication, "candidates", 20_000),
        (adjudication, "decisions", 20_000),
        (adjudication, "diagnostics", 1_000),
    ):
        rows = _field(layer, name, ())
        if not isinstance(rows, (tuple, list)) or len(rows) > limit:
            raise CorruptGraphError("graph record budget exceeded or invalid")
    total_options = 0
    for candidate in _field(adjudication, "candidates", ()):
        options = _field(candidate, "options", ())
        if not isinstance(options, (tuple, list)) or len(options) > 255:
            raise CorruptGraphError("candidate option budget exceeded or invalid")
        total_options += len(options)
        if total_options > MAX_TOTAL_OPTIONS:
            raise CorruptGraphError("aggregate candidate option budget exceeded")

    # Iterator frames keep the preflight's auxiliary memory proportional to depth.
    pending: list[Iterator[Any]] = [iter((document,))]
    count = size = 0
    while pending:
        try:
            value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        count += 1
        if count > MAX_VALUE_ITEMS or len(pending) > MAX_VALUE_DEPTH:
            raise CorruptGraphError("graph value budget exceeded")
        if isinstance(value, str):
            if len(value) > MAX_TEXT_LENGTH:
                raise CorruptGraphError("graph text budget exceeded")
            size += len(value.encode("utf-8"))
            if size > MAX_ARTIFACT_BYTES:
                raise CorruptGraphError("graph text byte budget exceeded")
        elif isinstance(value, Mapping):
            pending.append(iter(item for pair in value.items() for item in pair))
        elif isinstance(value, (tuple, list)):
            pending.append(iter(value))
        elif is_dataclass(value) and not isinstance(value, type):
            pending.append(
                iter(tuple(getattr(value, field.name) for field in fields(value)))
            )


def canonical_json(document: GraphDocument) -> bytes:
    """Encode a graph as byte-stable UTF-8 JSON."""
    _ensure_supported_schema(document.schema_version)
    return json.dumps(
        document.to_dict(),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def dump_graph(document: GraphDocument, path: Path) -> None:
    """Atomically replace ``path`` with a canonical graph artifact."""
    payload = canonical_json(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except OSError:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise


def load_graph(path: Path) -> GraphDocument:
    """Load a supported graph artifact or raise an explicit schema/data error."""
    try:
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > MAX_ARTIFACT_BYTES:
                raise CorruptGraphError("graph artifact byte budget exceeded")
            payload = stream.read(MAX_ARTIFACT_BYTES + 1)
        if len(payload) > MAX_ARTIFACT_BYTES:
            raise CorruptGraphError("graph artifact byte budget exceeded")
        decoded: Any = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise CorruptGraphError(f"cannot read graph artifact: {error}") from error
    if not isinstance(decoded, Mapping):
        raise CorruptGraphError("graph document must be a JSON object")
    schema_version = decoded.get("schema_version")
    if not isinstance(schema_version, str):
        raise CorruptGraphError("graph document is missing schema_version")
    _ensure_supported_schema(schema_version)
    try:
        validate_graph_limits(decoded)
        return GraphDocument.from_dict(decoded)
    except (TypeError, ValueError, RecursionError) as error:
        raise CorruptGraphError(f"invalid graph document: {error}") from error


def _ensure_supported_schema(schema_version: str) -> None:
    if schema_version != SCHEMA_VERSION:
        raise IncompatibleSchemaError(
            f"unsupported graph schema {schema_version!r}; expected {SCHEMA_VERSION!r}"
        )
