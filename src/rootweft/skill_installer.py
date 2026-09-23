"""Install the canonical Rootweft Agent Skill into harness skill directories.

The installer only ever writes inside ``<base>/skills/rootweft`` (plus a
sibling backup directory when replacing foreign content). It never edits
``AGENTS.md``, ``CLAUDE.md``, ``GEMINI.md``, MCP client configuration or shell
profiles; for MCP it only *prints* configuration snippets.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import Literal

from rootweft import __version__

SKILL_NAME = "rootweft"
MANIFEST_NAME = ".rootweft-skill-manifest.json"
MANIFEST_SCHEMA = "rootweft.skill-manifest.v1"
Scope = Literal["project", "user"]


class SkillTarget(StrEnum):
    """Supported harness conventions for Agent Skill directories."""

    AGENTS = "agents"  # Codex, Gemini CLI and other .agents-compatible clients
    CLAUDE = "claude"  # Claude Code
    HERMES = "hermes"  # Hermes Agent
    CURSOR = "cursor"  # Cursor


_BASES = {
    SkillTarget.AGENTS: ".agents",
    SkillTarget.CLAUDE: ".claude",
    SkillTarget.HERMES: ".hermes",
    SkillTarget.CURSOR: ".cursor",
}


class SkillInstallError(ValueError):
    """The requested install/uninstall would be unsafe or cannot be applied."""


@dataclass(frozen=True)
class InstallResult:
    target: str
    scope: str
    destination: Path
    files: tuple[str, ...]
    changed: bool
    dry_run: bool
    backup: Path | None

    def to_dict(self) -> dict[str, object]:
        return {
            "action": "install",
            "target": self.target,
            "scope": self.scope,
            "destination": str(self.destination),
            "files": list(self.files),
            "changed": self.changed,
            "dry_run": self.dry_run,
            "backup": None if self.backup is None else str(self.backup),
        }


@dataclass(frozen=True)
class UninstallResult:
    target: str
    scope: str
    destination: Path
    removed: tuple[str, ...]
    kept: tuple[str, ...]
    dry_run: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "action": "uninstall",
            "target": self.target,
            "scope": self.scope,
            "destination": str(self.destination),
            "removed": list(self.removed),
            "kept": list(self.kept),
            "dry_run": self.dry_run,
        }


def canonical_skill_files() -> dict[str, bytes]:
    """Return the packaged skill files (the single source of truth)."""
    root = files("rootweft").joinpath("skills", SKILL_NAME)
    return {
        item.name: item.read_bytes()
        for item in sorted(root.iterdir(), key=lambda entry: entry.name)
        if item.is_file() and not item.name.startswith((".", "_"))
    }


def skill_destination(
    target: SkillTarget | str,
    scope: Scope = "project",
    *,
    project: Path | None = None,
    home: Path | None = None,
    directory: Path | None = None,
) -> Path:
    """Resolve where a target/scope pair keeps the Rootweft skill."""
    target = SkillTarget(target)
    if directory is not None:
        if target is not SkillTarget.HERMES:
            raise SkillInstallError("a custom directory is supported only for hermes")
        return Path(directory) / SKILL_NAME
    if scope == "project":
        base = Path.cwd() if project is None else Path(project)
    elif scope == "user":
        base = Path.home() if home is None else Path(home)
    else:
        raise SkillInstallError("scope must be project or user")
    return base / _BASES[target] / "skills" / SKILL_NAME


def install_skill(
    target: SkillTarget | str,
    scope: Scope = "project",
    *,
    project: Path | None = None,
    home: Path | None = None,
    directory: Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
    stamp: str | None = None,
) -> InstallResult:
    """Install (or refresh) the skill, backing up any foreign content first."""
    target = SkillTarget(target)
    destination = skill_destination(
        target, scope, project=project, home=home, directory=directory
    )
    _refuse_links(destination)
    payload = canonical_skill_files()
    manifest = _manifest_bytes(target, payload)
    wanted = {**payload, MANIFEST_NAME: manifest}
    names = tuple(sorted(payload))

    existing = _existing_files(destination)
    if existing == wanted:
        return InstallResult(
            target.value, scope, destination, names, False, dry_run, None
        )
    backup_path: Path | None = None
    if existing is not None and not _only_unmodified_install(destination, existing):
        if not backup:
            raise SkillInstallError(
                "destination has content not installed by rootweft; allow a backup"
            )
        backup_path = _backup_path(destination, stamp)
    if dry_run:
        return InstallResult(
            target.value, scope, destination, names, True, True, backup_path
        )

    if backup_path is not None:
        os.replace(destination, backup_path)
    elif existing is not None:
        for name in existing:
            if name not in wanted:
                (destination / name).unlink()
    destination.mkdir(parents=True, exist_ok=True)
    _refuse_links(destination)
    for name, content in sorted(payload.items()):
        _atomic_write(destination / name, content)
    _atomic_write(destination / MANIFEST_NAME, manifest)
    return InstallResult(
        target.value, scope, destination, names, True, False, backup_path
    )


def uninstall_skill(
    target: SkillTarget | str,
    scope: Scope = "project",
    *,
    project: Path | None = None,
    home: Path | None = None,
    directory: Path | None = None,
    dry_run: bool = False,
) -> UninstallResult:
    """Remove only files recorded in the installation manifest and unmodified."""
    target = SkillTarget(target)
    destination = skill_destination(
        target, scope, project=project, home=home, directory=directory
    )
    _refuse_links(destination)
    recorded = _read_manifest(destination)
    removed: list[str] = []
    kept: list[str] = []
    for name, digest in sorted(recorded.items()):
        path = destination / name
        if not path.exists():
            continue
        if _sha256(path.read_bytes()) == digest:
            removed.append(name)
        else:
            kept.append(name)
    removed.append(MANIFEST_NAME)
    if not dry_run:
        for name in removed:
            (destination / name).unlink()
        try:
            destination.rmdir()
        except OSError:
            pass  # user files remain; leave the directory in place
    return UninstallResult(
        target.value,
        scope,
        destination,
        tuple(sorted(removed)),
        tuple(kept),
        dry_run,
    )


def mcp_config_snippet(client: str, graph: Path) -> str:
    """Return a copyable MCP stdio configuration; nothing is written anywhere."""
    graph_path = Path(graph).resolve().as_posix()
    args = ["mcp", graph_path]
    if client == "codex":
        return (
            f'[mcp_servers.rootweft]\ncommand = "rootweft"\nargs = {json.dumps(args)}\n'
        )
    if client == "hermes":
        return (
            "mcp_servers:\n"
            "  rootweft:\n"
            "    command: rootweft\n"
            f"    args: {json.dumps(args)}\n"
        )
    if client in {"claude", "gemini", "generic"}:
        document = {"mcpServers": {"rootweft": {"command": "rootweft", "args": args}}}
        return json.dumps(document, indent=2) + "\n"
    raise SkillInstallError("client must be codex, claude, gemini, hermes or generic")


def _manifest_bytes(target: SkillTarget, payload: dict[str, bytes]) -> bytes:
    document = {
        "schema": MANIFEST_SCHEMA,
        "rootweft_version": __version__,
        "target": target.value,
        "files": {name: _sha256(content) for name, content in sorted(payload.items())},
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_manifest(destination: Path) -> dict[str, str]:
    path = destination / MANIFEST_NAME
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SkillInstallError("no rootweft installation manifest found") from None
    except (OSError, UnicodeError, ValueError):
        raise SkillInstallError("installation manifest is unreadable") from None
    recorded = document.get("files") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("schema") != MANIFEST_SCHEMA
        or not isinstance(recorded, dict)
    ):
        raise SkillInstallError("installation manifest is invalid")
    for name, digest in recorded.items():
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or name in {"", ".", "..", MANIFEST_NAME}
            or "/" in name
            or "\\" in name
            or ":" in name
        ):
            raise SkillInstallError("installation manifest is invalid")
    return dict(recorded)


def _existing_files(destination: Path) -> dict[str, bytes] | None:
    if not destination.exists():
        return None
    if not destination.is_dir():
        raise SkillInstallError("destination exists and is not a directory")
    contents: dict[str, bytes] = {}
    for entry in destination.iterdir():
        if entry.is_symlink() or not entry.is_file():
            contents[entry.name] = b"\0not-a-regular-file"
        else:
            contents[entry.name] = entry.read_bytes()
    return contents


def _only_unmodified_install(destination: Path, existing: dict[str, bytes]) -> bool:
    if MANIFEST_NAME not in existing:
        return False
    try:
        recorded = _read_manifest(destination)
    except SkillInstallError:
        return False
    for name, content in existing.items():
        if name == MANIFEST_NAME:
            continue
        if recorded.get(name) != _sha256(content):
            return False
    return True


def _backup_path(destination: Path, stamp: str | None) -> Path:
    label = stamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = destination.with_name(f"{destination.name}.backup-{label}")
    counter = 1
    while candidate.exists() or candidate.is_symlink():
        counter += 1
        candidate = destination.with_name(
            f"{destination.name}.backup-{label}-{counter}"
        )
    return candidate


def _refuse_links(destination: Path) -> None:
    """Refuse symlinks/junctions in the skill directory and its skills parents."""
    for path in (destination, destination.parent, destination.parent.parent):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SkillInstallError("skill destination cannot use links")


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".rootweft-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
