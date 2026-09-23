"""Release tooling builds, checksums, describes and verifies real artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
TAG = "v0.1.0-alpha"


def load(name: str) -> ModuleType:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"release_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


build_release = load("build_release")
verify_release = load("verify_release")
generate_sbom = load("generate_sbom")

pytest.importorskip("build")
pytest.importorskip("mcp")


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    target = tmp_path_factory.mktemp("release") / "dist"
    build_release.build(target, allow_dirty=True, sbom_python=sys.executable)
    return target


def test_build_produces_exactly_the_release_files(dist: Path) -> None:
    version = verify_release.package_version()
    assert {path.name for path in dist.iterdir()} == verify_release.expected_files(
        TAG, version
    )


def test_checksums_match_bytes_and_are_sorted(dist: Path) -> None:
    lines = (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    names = [line.split("  ", 1)[1] for line in lines]
    assert names == sorted(names) and len(names) == 3
    for line in lines:
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((dist / name).read_bytes()).hexdigest() == digest
    verify_release.check_checksums(dist)


def test_sbom_is_cyclonedx_with_runtime_and_optional_scopes(dist: Path) -> None:
    sbom = json.loads(next(dist.glob("*.cdx.json")).read_text(encoding="utf-8"))
    assert sbom["bomFormat"] == "CycloneDX" and sbom["specVersion"] == "1.6"
    assert sbom["metadata"]["component"]["name"] == "rootweft"
    scopes = {item["name"].lower(): item["scope"] for item in sbom["components"]}
    assert scopes["httpx"] == "required"
    assert scopes["tree-sitter-typescript"] == "required"
    assert scopes["mcp"] == "optional"
    assert all("licenses" in item for item in sbom["components"])
    wheel = next(dist.glob("*.whl"))
    assert sbom["metadata"]["component"]["hashes"][0]["content"] == (
        hashlib.sha256(wheel.read_bytes()).hexdigest()
    )


def test_verify_accepts_built_release_and_installs_wheel(dist: Path) -> None:
    verify_release.verify(dist, TAG, allow_dirty=True, offline_smoke=True)


def test_verify_rejects_checksum_mismatch(dist: Path, tmp_path: Path) -> None:
    copy = tmp_path / "dist"
    shutil.copytree(dist, copy)
    sbom = next(copy.glob("*.cdx.json"))
    sbom.write_text(sbom.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(verify_release.ReleaseError, match="checksum mismatch"):
        verify_release.check_checksums(copy)


def test_verify_rejects_unexpected_files(dist: Path, tmp_path: Path) -> None:
    copy = tmp_path / "dist"
    shutil.copytree(dist, copy)
    (copy / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(verify_release.ReleaseError, match="unexpected release files"):
        verify_release.verify(copy, TAG, allow_dirty=True, smoke=False)


def test_verify_rejects_tag_version_mismatch(dist: Path) -> None:
    with pytest.raises(verify_release.ReleaseError, match="does not match"):
        verify_release.verify(dist, "v0.2.0-alpha", allow_dirty=True, smoke=False)


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v0.1.0-alpha", "0.1.0a1"),
        ("v0.1.0-alpha.2", "0.1.0a2"),
        ("v1.2.0-beta.3", "1.2.0b3"),
        ("v1.2.0-rc.1", "1.2.0rc1"),
        ("v1.0.0", "1.0.0"),
    ],
)
def test_tag_mapping(tag: str, version: str) -> None:
    assert verify_release.tag_to_version(tag) == version


def test_dirty_worktree_is_rejected(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "untracked.txt").write_text("x", encoding="utf-8")
    assert not build_release.worktree_is_clean(tmp_path)
    with pytest.raises(verify_release.ReleaseError, match="dirty"):
        verify_release.check_clean(tmp_path)


def test_public_documents_exist_with_working_links() -> None:
    verify_release.check_docs(ROOT)


def test_broken_markdown_link_is_reported(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[missing](docs/NOPE.md)\n", "utf-8")
    with pytest.raises(verify_release.ReleaseError, match="broken link"):
        verify_release.check_markdown_links(tmp_path)


def test_sbom_serial_is_deterministic() -> None:
    components = generate_sbom.probe(sys.executable, "mcp")
    from datetime import UTC, datetime

    when = datetime(2026, 9, 23, tzinfo=UTC)
    first = generate_sbom.build_sbom(components, timestamp=when, artifacts={"a": b"1"})
    second = generate_sbom.build_sbom(
        list(reversed(components)), timestamp=when, artifacts={"a": b"1"}
    )
    assert first == second
