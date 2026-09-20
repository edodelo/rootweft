"""Safe, deterministic repository scanning without executing repository code."""

from __future__ import annotations

import fnmatch
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath

from rootweft.models import Diagnostic, Evidence

_DEFAULT_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "vendor",
        "__pycache__",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".rootweft",
    }
)
_CREDENTIAL_NAMES = frozenset(
    {
        ".env",
        ".envrc",
        "credentials",
        "credentials.json",
        "credentials.yaml",
        "credentials.yml",
        "secrets",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)
_CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".mdx": "markdown",
}
_MAX_DIAGNOSTICS = 100


@dataclass(frozen=True)
class ScanLimits:
    """Bounded resource budget for a single repository scan."""

    max_files: int = 10_000
    max_file_bytes: int = 2_000_000
    max_total_bytes: int = 100_000_000

    def __post_init__(self) -> None:
        for name, value in (
            ("max_files", self.max_files),
            ("max_file_bytes", self.max_file_bytes),
            ("max_total_bytes", self.max_total_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ScannedFile:
    """Immutable text source returned by a completed safe read."""

    path: str
    language: str
    text: str
    sha256: str

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        if not self.language:
            raise ValueError("language must not be empty")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class ScanResult:
    """The scanned files and bounded, content-free diagnostics."""

    files: tuple[ScannedFile, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


def scan_repository(
    root: Path,
    limits: ScanLimits,
    extra_ignores: tuple[str, ...] = (),
) -> ScanResult:
    """Return safe text files below ``root`` in deterministic POSIX-path order.

    The only path canonicalization is for the caller-provided root. Descendants
    are reached through ``scandir`` and links are never traversed.
    """
    if not isinstance(root, Path):
        raise TypeError("root must be a pathlib.Path")
    if not isinstance(limits, ScanLimits):
        raise TypeError("limits must be a ScanLimits instance")
    if not all(isinstance(pattern, str) and pattern for pattern in extra_ignores):
        raise ValueError("extra_ignores must contain non-empty strings")

    try:
        canonical_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("scan root must exist and be resolvable") from error
    try:
        root_mode = canonical_root.stat().st_mode
    except OSError as error:
        raise ValueError("scan root must be readable") from error
    if not stat.S_ISDIR(root_mode):
        raise ValueError("scan root must be a directory")

    files: list[ScannedFile] = []
    diagnostics: list[Diagnostic] = []
    total_bytes = 0

    discovered_files = sorted(
        _walk_files(canonical_root, extra_ignores, diagnostics),
        key=lambda item: item[1],
    )
    for disk_path, relative_path in discovered_files:
        if len(files) >= limits.max_files:
            _add_diagnostic(diagnostics, "max_files", relative_path)
            break

        try:
            entry_stat = os.stat(disk_path, follow_symlinks=False)
        except FileNotFoundError:
            _add_diagnostic(diagnostics, "disappeared_file", relative_path)
            continue
        except PermissionError:
            _add_diagnostic(diagnostics, "permission_denied", relative_path)
            continue
        except OSError:
            _add_diagnostic(diagnostics, "unreadable_file", relative_path)
            continue

        if not stat.S_ISREG(entry_stat.st_mode):
            _add_diagnostic(diagnostics, "special_file", relative_path)
            continue
        if entry_stat.st_size > limits.max_file_bytes:
            _add_diagnostic(diagnostics, "max_file_bytes", relative_path)
            continue
        if total_bytes + entry_stat.st_size > limits.max_total_bytes:
            _add_diagnostic(diagnostics, "max_total_bytes", relative_path)
            break

        raw = _read_regular_file(
            disk_path,
            relative_path,
            entry_stat,
            limits.max_file_bytes,
            diagnostics,
        )
        if raw is None:
            continue
        if len(raw) > limits.max_file_bytes:
            _add_diagnostic(diagnostics, "max_file_bytes", relative_path)
            continue
        if total_bytes + len(raw) > limits.max_total_bytes:
            _add_diagnostic(diagnostics, "max_total_bytes", relative_path)
            break
        if b"\x00" in raw:
            _add_diagnostic(diagnostics, "binary_file", relative_path)
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            _add_diagnostic(diagnostics, "invalid_encoding", relative_path)
            continue

        files.append(
            ScannedFile(
                path=relative_path,
                language=_language_for(relative_path),
                text=text,
                sha256=sha256(raw).hexdigest(),
            )
        )
        total_bytes += len(raw)

    return ScanResult(files=tuple(files), diagnostics=tuple(diagnostics))


def _walk_files(
    root: Path,
    extra_ignores: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> Iterator[tuple[Path, str]]:
    pending: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while pending:
        directory, relative_directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                ordered_entries = sorted(entries, key=lambda entry: entry.name)
        except PermissionError:
            _add_diagnostic(
                diagnostics, "permission_denied", _display_path(relative_directory)
            )
            continue
        except FileNotFoundError:
            _add_diagnostic(
                diagnostics, "disappeared_directory", _display_path(relative_directory)
            )
            continue
        except OSError:
            _add_diagnostic(
                diagnostics, "unreadable_directory", _display_path(relative_directory)
            )
            continue

        child_directories: list[tuple[Path, PurePosixPath]] = []
        for entry in ordered_entries:
            relative = relative_directory / entry.name
            relative_path = _display_path(relative)
            if _is_ignored(relative, entry.name, extra_ignores):
                continue
            try:
                if entry.is_symlink():
                    _add_diagnostic(diagnostics, "symlink_skipped", relative_path)
                    continue
                entry_stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                _add_diagnostic(diagnostics, "disappeared_file", relative_path)
                continue
            except PermissionError:
                _add_diagnostic(diagnostics, "permission_denied", relative_path)
                continue
            except OSError:
                _add_diagnostic(diagnostics, "unreadable_file", relative_path)
                continue

            if stat.S_ISDIR(entry_stat.st_mode):
                child_directories.append((Path(entry.path), relative))
            elif stat.S_ISREG(entry_stat.st_mode):
                yield Path(entry.path), relative_path
            else:
                _add_diagnostic(diagnostics, "special_file", relative_path)

        pending.extend(reversed(child_directories))


def _read_regular_file(
    path: Path,
    relative_path: str,
    expected_stat: os.stat_result,
    max_file_bytes: int,
    diagnostics: list[Diagnostic],
) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | nofollow)
    except FileNotFoundError:
        _add_diagnostic(diagnostics, "disappeared_file", relative_path)
        return None
    except PermissionError:
        _add_diagnostic(diagnostics, "permission_denied", relative_path)
        return None
    except OSError:
        _add_diagnostic(diagnostics, "unreadable_file", relative_path)
        return None

    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or not _same_file(
            expected_stat, opened_stat
        ):
            _add_diagnostic(diagnostics, "changed_file", relative_path)
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read(max_file_bytes + 1)
    except OSError:
        _add_diagnostic(diagnostics, "unreadable_file", relative_path)
        return None
    finally:
        os.close(descriptor)


def _same_file(before: os.stat_result, after: os.stat_result) -> bool:
    return before.st_dev == after.st_dev and before.st_ino == after.st_ino


def _is_ignored(
    relative: PurePosixPath, name: str, extra_ignores: tuple[str, ...]
) -> bool:
    lower_name = name.casefold()
    if (
        lower_name in _DEFAULT_IGNORED_NAMES
        or lower_name in _CREDENTIAL_NAMES
        or lower_name.startswith(".env.")
    ):
        return True
    if lower_name.endswith(_CREDENTIAL_SUFFIXES):
        return True
    path = _display_path(relative)
    return any(
        fnmatch.fnmatchcase(path, pattern) or relative.match(pattern)
        for pattern in extra_ignores
    )


def _language_for(path: str) -> str:
    return _LANGUAGES.get(PurePosixPath(path).suffix.casefold(), "text")


def _display_path(path: PurePosixPath) -> str:
    return "" if path == PurePosixPath(".") else path.as_posix()


def _add_diagnostic(diagnostics: list[Diagnostic], code: str, path: str) -> None:
    if len(diagnostics) >= _MAX_DIAGNOSTICS:
        return
    evidence = Evidence(path, 1, 1) if path else None
    diagnostics.append(
        Diagnostic(code=code, message=f"{code}: {path}", evidence=evidence)
    )


def _validate_relative_path(path: str) -> None:
    posix = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or posix.is_absolute()
        or PureWindowsPath(path).is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise ValueError("path must be a relative POSIX path")
