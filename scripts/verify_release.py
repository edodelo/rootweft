"""Verify Rootweft release artifacts before they are published.

Checks: clean worktree, tag/version agreement, exact file set, checksums,
wheel and sdist metadata and contents, CycloneDX SBOM structure, required
public documentation, local Markdown links, and (unless --no-smoke) a clean
virtual-environment install that runs ``rootweft --version`` and builds the
sample graph.

Usage:
    python scripts/verify_release.py [--dist dist] [--tag v0.1.0-alpha]
                                     [--allow-dirty] [--no-smoke]
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_DOCS = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "ACKNOWLEDGMENTS",
    "THIRD_PARTY_NOTICES.md",
    "PROVENANCE.md",
    "SECURITY.md",
    "PRIVACY.md",
    "DATA_EGRESS.md",
    "THREAT_MODEL.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "CHANGELOG.md",
    "docs/USAGE.md",
    "docs/MCP.md",
    "docs/HARNESS_COMPATIBILITY.md",
    "docs/JEV.md",
    "docs/RELEASE.md",
)
WHEEL_MEMBERS = (
    "rootweft/cli.py",
    "rootweft/mcp_server.py",
    "rootweft/py.typed",
    "rootweft/assets/viewer.html",
    "rootweft/skills/rootweft/SKILL.md",
)


class ReleaseError(Exception):
    """A release check failed."""


def package_version(root: Path = ROOT) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def tag_to_version(tag: str) -> str:
    """Map a human tag (v0.1.0-alpha, v1.2.0-rc.1, v1.0.0) to PEP 440."""
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)(?:-(alpha|beta|rc)(?:\.(\d+))?)?", tag)
    if not match:
        raise ReleaseError(f"unsupported tag format: {tag}")
    base, stage, number = match.groups()
    if stage is None:
        return base
    marker = {"alpha": "a", "beta": "b", "rc": "rc"}[stage]
    return f"{base}{marker}{int(number or 1)}"


def expected_files(tag: str, version: str) -> set[str]:
    return {
        f"rootweft-{version}-py3-none-any.whl",
        f"rootweft-{version}.tar.gz",
        f"rootweft-{tag.removeprefix('v')}.cdx.json",
        "SHA256SUMS",
    }


def check_file_set(dist: Path, tag: str, version: str) -> None:
    present = {path.name for path in dist.iterdir()}
    wanted = expected_files(tag, version)
    if present != wanted:
        raise ReleaseError(
            f"unexpected release files: missing={sorted(wanted - present)} "
            f"extra={sorted(present - wanted)}"
        )


def check_checksums(dist: Path) -> None:
    lines = (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    listed = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line)
        if not match:
            raise ReleaseError(f"malformed checksum line: {line!r}")
        listed[match.group(2)] = match.group(1)
    if list(listed) != sorted(listed):
        raise ReleaseError("checksum lines are not sorted")
    files = {p.name for p in dist.iterdir() if p.name != "SHA256SUMS"}
    if set(listed) != files:
        raise ReleaseError("checksum file does not cover exactly the release files")
    for name, digest in listed.items():
        if hashlib.sha256((dist / name).read_bytes()).hexdigest() != digest:
            raise ReleaseError(f"checksum mismatch: {name}")


def check_wheel(wheel: Path, version: str) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        metadata_name = f"rootweft-{version}.dist-info/METADATA"
        if metadata_name not in names:
            raise ReleaseError("wheel metadata missing")
        metadata = email.parser.Parser().parsestr(
            archive.read(metadata_name).decode("utf-8")
        )
        for member in WHEEL_MEMBERS:
            if member not in names:
                raise ReleaseError(f"wheel is missing {member}")
        for license_file in ("LICENSE", "NOTICE"):
            if f"rootweft-{version}.dist-info/licenses/{license_file}" not in names:
                raise ReleaseError(f"wheel is missing {license_file}")
        if any(name.startswith(("tests/", "scripts/", ".")) for name in names):
            raise ReleaseError("wheel contains repository-only files")
    if metadata["Name"] != "rootweft" or metadata["Version"] != version:
        raise ReleaseError("wheel name/version mismatch")
    if metadata.get("License-Expression") != "Apache-2.0":
        raise ReleaseError("wheel license expression must be Apache-2.0")
    if metadata.get("Requires-Python") != "<3.14,>=3.11":
        raise ReleaseError("unexpected Requires-Python")
    extras = metadata.get_all("Provides-Extra") or []
    if "mcp" not in extras:
        raise ReleaseError("wheel does not provide the mcp extra")


def check_sdist(sdist: Path, version: str) -> None:
    prefix = f"rootweft-{version}/"
    with tarfile.open(sdist) as archive:
        names = set(archive.getnames())
    for document in (*REQUIRED_DOCS, "pyproject.toml", "skills/rootweft/SKILL.md"):
        if prefix + document not in names:
            raise ReleaseError(f"sdist is missing {document}")
    if any("/.superpowers/" in name or "/.venv/" in name for name in names):
        raise ReleaseError("sdist contains private working files")


def check_sbom(path: Path, version: str) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.6":
        raise ReleaseError("SBOM is not CycloneDX 1.6")
    component = document["metadata"]["component"]
    if component["name"] != "rootweft" or component["version"] != version:
        raise ReleaseError("SBOM describes the wrong component")
    names = {item["name"].lower() for item in document["components"]}
    for required in ("httpx", "tree-sitter", "tree-sitter-javascript", "mcp"):
        if required not in names:
            raise ReleaseError(f"SBOM is missing {required}")
    for item in document["components"]:
        if item.get("scope") not in {"required", "optional"}:
            raise ReleaseError("SBOM component without scope")


def check_docs(root: Path = ROOT) -> None:
    for document in REQUIRED_DOCS:
        if not (root / document).is_file():
            raise ReleaseError(f"missing documentation: {document}")
    check_markdown_links(root)


_SKIP_DIRS = frozenset({"build", "dist", "examples", "tests"})
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def check_markdown_links(root: Path = ROOT) -> None:
    for path in sorted(root.rglob("*.md")):
        parts = path.relative_to(root).parts
        relative = "/".join(parts)
        if any(part.startswith(".") for part in parts) or parts[0] in _SKIP_DIRS:
            continue
        if "node_modules" in parts:
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in _LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue
            local = target.split("#", 1)[0]
            if local and not (path.parent / local).exists():
                raise ReleaseError(f"broken link in {relative}: {target}")


def check_clean(root: Path = ROOT) -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ReleaseError("worktree is dirty")


def smoke_install(wheel: Path, *, offline: bool = False) -> None:
    """Install the wheel into a clean venv and exercise the console script."""
    with tempfile.TemporaryDirectory(prefix="rootweft-verify-") as scratch:
        environment = Path(scratch) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        bindir = environment / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        install = [str(python), "-m", "pip", "install", "--quiet"]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        if offline:
            # Dependencies come from this interpreter; Rootweft from the wheel.
            import site

            install += ["--no-deps", "--no-index"]
            env["PYTHONPATH"] = os.pathsep.join(site.getsitepackages())
        subprocess.run([*install, str(wheel)], check=True, capture_output=True)
        executable = bindir / ("rootweft.exe" if os.name == "nt" else "rootweft")
        version = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        if version != f"rootweft {package_version()}":
            raise ReleaseError(f"installed console script reports {version!r}")
        location = subprocess.run(
            [str(python), "-c", "import rootweft; print(rootweft.__file__)"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout.strip()
        if not Path(location).resolve().is_relative_to(environment.resolve()):
            raise ReleaseError("smoke test did not import the installed wheel")
        graph = Path(scratch) / "graph.json"
        subprocess.run(
            [
                str(executable),
                "build",
                str(ROOT / "examples" / "sample_repo"),
                "--output",
                str(graph),
            ],
            check=True,
            capture_output=True,
            cwd=scratch,
            env=env,
        )
        document = json.loads(graph.read_text(encoding="utf-8"))
        if (
            document["schema_version"] != "rootweft.graph.v1"
            or not document["structural"]["nodes"]
        ):
            raise ReleaseError("installed wheel did not build the sample graph")


def verify(
    dist: Path,
    tag: str,
    *,
    allow_dirty: bool = False,
    smoke: bool = True,
    offline_smoke: bool = False,
) -> None:
    version = package_version()
    if tag_to_version(tag) != version:
        raise ReleaseError(f"tag {tag} does not match package version {version}")
    if not allow_dirty:
        check_clean()
    check_file_set(dist, tag, version)
    check_checksums(dist)
    wheel = dist / f"rootweft-{version}-py3-none-any.whl"
    check_wheel(wheel, version)
    check_sdist(dist / f"rootweft-{version}.tar.gz", version)
    check_sbom(dist / f"rootweft-{tag.removeprefix('v')}.cdx.json", version)
    check_docs()
    if smoke:
        smoke_install(wheel, offline=offline_smoke)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify Rootweft release artifacts.")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", default="v0.1.0-alpha")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--no-smoke", action="store_true")
    parser.add_argument(
        "--offline-smoke",
        action="store_true",
        help="install without dependencies into a venv that sees this interpreter",
    )
    args = parser.parse_args(argv)
    try:
        verify(
            args.dist,
            args.tag,
            allow_dirty=args.allow_dirty,
            smoke=not args.no_smoke,
            offline_smoke=args.offline_smoke,
        )
    except ReleaseError as error:
        print(f"release verification failed: {error}", file=sys.stderr)
        return 1
    print(f"release verified: {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
