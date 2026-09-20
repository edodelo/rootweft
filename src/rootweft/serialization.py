"""Canonical and atomic persistence for Rootweft graph documents."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rootweft.errors import CorruptGraphError, IncompatibleSchemaError
from rootweft.models import SCHEMA_VERSION, GraphDocument


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
        decoded: Any = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CorruptGraphError(f"cannot read graph artifact: {error}") from error
    if not isinstance(decoded, Mapping):
        raise CorruptGraphError("graph document must be a JSON object")
    schema_version = decoded.get("schema_version")
    if not isinstance(schema_version, str):
        raise CorruptGraphError("graph document is missing schema_version")
    _ensure_supported_schema(schema_version)
    try:
        return GraphDocument.from_dict(decoded)
    except (TypeError, ValueError) as error:
        raise CorruptGraphError(f"invalid graph document: {error}") from error


def _ensure_supported_schema(schema_version: str) -> None:
    if schema_version != SCHEMA_VERSION:
        raise IncompatibleSchemaError(
            f"unsupported graph schema {schema_version!r}; expected {SCHEMA_VERSION!r}"
        )
