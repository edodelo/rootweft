"""Generate a deterministic CycloneDX 1.6 SBOM for an installed Rootweft.

The dependency closure is read from package metadata inside a target Python
environment (``--python``) in which Rootweft (optionally with extras) is
installed. Components needed only by an extra are marked ``optional``.

Usage:
    python scripts/generate_sbom.py --python .venv/bin/python --output sbom.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SPEC_VERSION = "1.6"
NAMESPACE = uuid.UUID("6f6f7477-6565-4674-8000-726f6f747765")

# Executed inside the target interpreter; prints JSON describing the closure.
_PROBE = r"""
import json, re, sys
import importlib.metadata as md
try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement

def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()

def closure(root, extras):
    seen, queue = {}, [(root, tuple(extras))]
    while queue:
        name, active = queue.pop()
        dist = md.distribution(name)
        key = norm(dist.metadata["Name"])
        if key in seen and set(active) <= set(seen[key][1]):
            continue
        previous = seen.get(key, (dist, ()))[1]
        seen[key] = (dist, tuple(sorted(set(previous) | set(active))))
        for text in dist.requires or ():
            req = Requirement(text)
            environments = [{"extra": e} for e in (active or ("",))]
            if req.marker is not None and not any(
                req.marker.evaluate(env) for env in environments
            ):
                continue
            queue.append((req.name, tuple(sorted(req.extras))))
    return {key: value[0] for key, value in seen.items()}

extras = [e for e in sys.argv[1].split(",") if e]
base = closure("rootweft", [])
full = closure("rootweft", extras)
out = []
for key, dist in sorted(full.items()):
    meta = dist.metadata
    classifiers = [
        c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or [])
        if c.startswith("License ::")
    ]
    out.append({
        "name": meta["Name"],
        "version": dist.version,
        "license_expression": meta.get("License-Expression"),
        "license_text": (meta.get("License") or "").strip().splitlines()[:1],
        "license_classifiers": classifiers,
        "summary": meta.get("Summary") or "",
        "optional": key not in base,
        "requires": sorted(
            {norm(Requirement(r).name) for r in (dist.requires or ())} & set(full)
        ),
    })
print(json.dumps(out))
"""

_SPDX_BY_CLASSIFIER = {
    "MIT License": "MIT",
    "BSD License": "BSD-3-Clause",
    "Apache Software License": "Apache-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "Python Software Foundation License": "PSF-2.0",
}


def _normalise(name: str) -> str:
    import re

    return re.sub(r"[-_.]+", "-", name).lower()


def _purl(name: str, version: str) -> str:
    return f"pkg:pypi/{_normalise(name)}@{version}"


def _licenses(entry: dict[str, Any]) -> list[dict[str, Any]]:
    expression = entry.get("license_expression")
    if expression:
        return [{"expression": expression}]
    names = [
        _SPDX_BY_CLASSIFIER.get(item)
        for item in entry.get("license_classifiers", [])
        if _SPDX_BY_CLASSIFIER.get(item)
    ]
    if names:
        return [{"license": {"id": name}} for name in sorted(set(names))]
    text = entry.get("license_text") or []
    if text and text[0] and len(text[0]) <= 64:
        return [{"license": {"name": text[0]}}]
    return []


def probe(python: str, extras: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [python, "-c", _PROBE, extras],
        check=True,
        capture_output=True,
        text=True,
    )
    result: list[dict[str, Any]] = json.loads(completed.stdout)
    return result


def build_sbom(
    components: list[dict[str, Any]],
    *,
    timestamp: datetime,
    artifacts: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    by_name = {_normalise(c["name"]): c for c in components}
    root = by_name.get("rootweft")
    if root is None:
        raise SystemExit("rootweft is not installed in the target environment")
    root_ref = _purl(root["name"], root["version"])
    hashes = sorted((artifacts or {}).items())
    serial_seed = root_ref + "".join(
        hashlib.sha256(content).hexdigest() for _, content in hashes
    )
    metadata_component: dict[str, Any] = {
        "type": "application",
        "bom-ref": root_ref,
        "name": "rootweft",
        "version": root["version"],
        "purl": root_ref,
        "licenses": _licenses(root),
        "externalReferences": [
            {"type": "vcs", "url": "https://github.com/edodelo/rootweft"}
        ],
    }
    wheels = [content for name, content in hashes if name.endswith(".whl")]
    if len(wheels) == 1:
        metadata_component["hashes"] = [
            {"alg": "SHA-256", "content": hashlib.sha256(wheels[0]).hexdigest()}
        ]
    items = []
    for key in sorted(by_name):
        if key == "rootweft":
            continue
        entry = by_name[key]
        ref = _purl(entry["name"], entry["version"])
        component: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": entry["name"],
            "version": entry["version"],
            "purl": ref,
            "scope": "optional" if entry["optional"] else "required",
        }
        licenses = _licenses(entry)
        if licenses:
            component["licenses"] = licenses
        items.append(component)
    dependencies = [
        {
            "ref": _purl(by_name[key]["name"], by_name[key]["version"]),
            "dependsOn": sorted(
                _purl(by_name[child]["name"], by_name[child]["version"])
                for child in by_name[key]["requires"]
                if child in by_name
            ),
        }
        for key in sorted(by_name)
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid5(NAMESPACE, serial_seed)}",
        "version": 1,
        "metadata": {
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "rootweft scripts/generate_sbom.py",
                        "version": root["version"],
                    }
                ]
            },
            "component": metadata_component,
        },
        "components": items,
        "dependencies": dependencies,
    }


def source_date() -> datetime:
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        return datetime.fromtimestamp(int(epoch), UTC)
    return datetime.now(UTC).replace(microsecond=0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--extras", default="mcp")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        type=Path,
        help="release file whose hash identifies this SBOM (repeatable)",
    )
    args = parser.parse_args(argv)
    components = probe(args.python, args.extras)
    artifacts = {path.name: path.read_bytes() for path in args.artifact}
    document = build_sbom(components, timestamp=source_date(), artifacts=artifacts)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
