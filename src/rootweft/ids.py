"""Stable identifiers for graph entities and evidence."""

from __future__ import annotations

import hashlib


def stable_id(*parts: str) -> str:
    """Return a SHA-256 identifier for unambiguously delimited string parts."""
    digest = hashlib.sha256()
    for part in parts:
        encoded = part.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def evidence_fingerprint(text: str) -> str:
    """Return a SHA-256 fingerprint for source evidence without persisting it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
