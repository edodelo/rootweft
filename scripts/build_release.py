"""Build Rootweft release artifacts: wheel, sdist, CycloneDX SBOM, SHA256SUMS.

Steps:
1. refuse a dirty Git worktree (unless --allow-dirty);
2. set SOURCE_DATE_EPOCH to the HEAD commit time for reproducible archives;
3. build the sdist and wheel with ``python -m build`` into an empty --dist;
4. install the wheel with the ``mcp`` extra into a fresh virtual environment
   and generate the SBOM from what was actually installed;
5. write sorted SHA-256 checksums for every release file.

Usage:
    python scripts/build_release.py [--dist dist] [--allow-dirty]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_sbom  # noqa: E402

TAG = "v0.1.0-alpha"
SBOM_NAME = f"rootweft-{TAG.removeprefix('v')}.cdx.json"
CHECKSUMS = "SHA256SUMS"


def run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return completed.stdout


def worktree_is_clean(root: Path = ROOT) -> bool:
    return (
        run("git", "status", "--porcelain", "--untracked-files=normal", cwd=root) == ""
    )


def commit_epoch(root: Path = ROOT) -> str:
    return run("git", "log", "-1", "--format=%ct", cwd=root).strip()


def write_checksums(dist: Path) -> Path:
    lines = []
    for path in sorted(dist.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != CHECKSUMS:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.name}\n")
    target = dist / CHECKSUMS
    target.write_text("".join(lines), encoding="utf-8", newline="\n")
    return target


def venv_python(directory: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return directory / scripts / f"python{suffix}"


def build(
    dist: Path, *, allow_dirty: bool = False, sbom_python: str | None = None
) -> None:
    if not allow_dirty and not worktree_is_clean():
        raise SystemExit("refusing to build a release from a dirty worktree")
    env = dict(os.environ)
    env.setdefault("SOURCE_DATE_EPOCH", commit_epoch())
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    run(
        sys.executable,
        "-m",
        "build",
        "--sdist",
        "--wheel",
        "--outdir",
        str(dist),
        env=env,
    )
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("expected exactly one wheel and one sdist")

    with tempfile.TemporaryDirectory(prefix="rootweft-sbom-") as scratch:
        python = sbom_python
        if python is None:
            environment = Path(scratch) / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(environment)
            python = str(venv_python(environment))
            run(python, "-m", "pip", "install", "--quiet", f"{wheels[0]}[mcp]")
        os.environ["SOURCE_DATE_EPOCH"] = env["SOURCE_DATE_EPOCH"]
        generate_sbom.main(
            [
                "--python",
                python,
                "--extras",
                "mcp",
                "--output",
                str(dist / SBOM_NAME),
                "--artifact",
                str(wheels[0]),
                "--artifact",
                str(sdists[0]),
            ]
        )
    write_checksums(dist)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Rootweft release artifacts.")
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--sbom-python",
        help="introspect this interpreter instead of a fresh venv (testing only)",
    )
    args = parser.parse_args(argv)
    build(args.dist, allow_dirty=args.allow_dirty, sbom_python=args.sbom_python)
    for line in (args.dist / CHECKSUMS).read_text(encoding="utf-8").splitlines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
