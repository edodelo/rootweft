"""Render an inert graph payload into an atomic, network-disabled HTML viewer."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import tempfile
from importlib.resources import files
from pathlib import Path

from rootweft.models import GraphDocument
from rootweft.query import GraphIndex
from rootweft.serialization import canonical_json


def render_viewer(
    document: GraphDocument, output: Path, max_visible: int = 500
) -> None:
    """Export a standalone viewer. Graph strings never enter executable markup."""
    if type(max_visible) is not int or not 1 <= max_visible <= 10_000:
        raise ValueError("max_visible must be between 1 and 10000")
    GraphIndex.from_document(document)
    payload = base64.b64encode(canonical_json(document)).decode("ascii")
    template = (
        files("rootweft").joinpath("assets/viewer.html").read_text(encoding="utf-8")
    )
    for tag in ("script", "style"):
        match = re.search(rf"<{tag}>(.*?)</{tag}>", template, flags=re.DOTALL)
        if match is None:
            raise RuntimeError("viewer resource is missing")
        digest = base64.b64encode(
            hashlib.sha256(match[1].encode("utf-8")).digest()
        ).decode("ascii")
        template = template.replace("__" + tag.upper() + "_HASH__", digest)
    html = template.replace("__GRAPH_BASE64__", payload).replace(
        "__MAX_VISIBLE__", str(max_visible)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(html.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
