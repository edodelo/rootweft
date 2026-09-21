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
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".git-credentials",
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
_WINDOWS_REPARSE_POINT = 0x400


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


@dataclass(frozen=True)
class _FileCandidate:
    path: str
    expected_stat: os.stat_result
    disk_path: Path | None = None
    parent_fd: int | None = None
    name: str | None = None


@dataclass(frozen=True)
class _WindowsRootAnchor:
    handle: int
    final_path: str


@dataclass
class _TraversalState:
    max_directory_entries: int
    exhausted: bool = False


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

    diagnostics: list[Diagnostic] = []
    windows_anchor = _open_windows_root_anchor(canonical_root)
    if os.name == "nt" and windows_anchor is None:
        _add_diagnostic(diagnostics, "unsafe_root_primitive", "")
        return ScanResult(diagnostics=tuple(diagnostics))

    files: list[ScannedFile] = []
    total_bytes = 0
    candidate_count = 0
    state = _TraversalState(max_directory_entries=limits.max_files + 1)
    try:
        for candidate in _walk_files(
            canonical_root,
            extra_ignores,
            diagnostics,
            state,
            windows_anchor,
        ):
            candidate_count += 1
            if candidate_count > limits.max_files:
                _add_diagnostic(diagnostics, "max_files", candidate.path)
                break
            if candidate.expected_stat.st_size > limits.max_file_bytes:
                _add_diagnostic(diagnostics, "max_file_bytes", candidate.path)
                continue
            remaining_bytes = limits.max_total_bytes - total_bytes
            if candidate.expected_stat.st_size > remaining_bytes:
                _add_diagnostic(diagnostics, "max_total_bytes", candidate.path)
                break

            raw = _read_regular_file(
                candidate,
                limits.max_file_bytes,
                remaining_bytes,
                windows_anchor,
                diagnostics,
            )
            if raw is None:
                continue
            total_bytes += len(raw)
            if b"\x00" in raw:
                _add_diagnostic(diagnostics, "binary_file", candidate.path)
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                _add_diagnostic(diagnostics, "invalid_encoding", candidate.path)
                continue

            files.append(
                ScannedFile(
                    path=candidate.path,
                    language=_language_for(candidate.path),
                    text=text,
                    sha256=sha256(raw).hexdigest(),
                )
            )
    finally:
        if windows_anchor is not None:
            _close_windows_handle(windows_anchor.handle)

    files.sort(key=lambda item: item.path)
    return ScanResult(files=tuple(files), diagnostics=tuple(diagnostics))


def _walk_files(
    root: Path,
    extra_ignores: tuple[str, ...],
    diagnostics: list[Diagnostic],
    state: _TraversalState,
    windows_anchor: _WindowsRootAnchor | None,
) -> Iterator[_FileCandidate]:
    if os.name == "nt":
        if windows_anchor is None:
            _add_diagnostic(diagnostics, "unsafe_root_primitive", "")
            return
        root_stat = _lstat_directory(root, "", diagnostics)
        if root_stat is not None:
            yield from _walk_files_windows(
                root,
                windows_anchor,
                PurePosixPath("."),
                root_stat,
                extra_ignores,
                diagnostics,
                state,
            )
        return

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        _add_diagnostic(diagnostics, "unsafe_directory_primitive", "")
        return
    try:
        root_fd = os.open(root, directory_flags | nofollow)
    except OSError:
        _add_diagnostic(diagnostics, "unreadable_directory", "")
        return
    try:
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            _add_diagnostic(diagnostics, "unreadable_directory", "")
            return
        yield from _walk_files_posix(
            root_fd, PurePosixPath("."), extra_ignores, diagnostics, state
        )
    finally:
        os.close(root_fd)


def _walk_files_posix(
    directory_fd: int,
    relative_directory: PurePosixPath,
    extra_ignores: tuple[str, ...],
    diagnostics: list[Diagnostic],
    state: _TraversalState,
) -> Iterator[_FileCandidate]:
    if state.exhausted:
        return
    try:
        with os.scandir(directory_fd) as entries:
            ordered_entries = _bounded_sorted_entries(
                entries, relative_directory, diagnostics, state
            )
    except PermissionError:
        _add_diagnostic(
            diagnostics, "permission_denied", _display_path(relative_directory)
        )
        return
    except FileNotFoundError:
        _add_diagnostic(
            diagnostics, "disappeared_directory", _display_path(relative_directory)
        )
        return
    except OSError:
        _add_diagnostic(
            diagnostics, "unreadable_directory", _display_path(relative_directory)
        )
        return
    if ordered_entries is None:
        return

    for entry in ordered_entries:
        if state.exhausted:
            return
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
            flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
            except FileNotFoundError:
                _add_diagnostic(diagnostics, "disappeared_directory", relative_path)
                continue
            except PermissionError:
                _add_diagnostic(diagnostics, "permission_denied", relative_path)
                continue
            except OSError:
                _add_diagnostic(diagnostics, "symlink_skipped", relative_path)
                continue
            try:
                opened_stat = os.fstat(child_fd)
                if not stat.S_ISDIR(opened_stat.st_mode) or not _same_file(
                    entry_stat, opened_stat
                ):
                    _add_diagnostic(diagnostics, "changed_directory", relative_path)
                    continue
                yield from _walk_files_posix(
                    child_fd, relative, extra_ignores, diagnostics, state
                )
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(entry_stat.st_mode):
            yield _FileCandidate(
                path=relative_path,
                expected_stat=entry_stat,
                parent_fd=directory_fd,
                name=entry.name,
            )
        else:
            _add_diagnostic(diagnostics, "special_file", relative_path)


def _walk_files_windows(
    directory: Path,
    root_anchor: _WindowsRootAnchor,
    relative_directory: PurePosixPath,
    expected_stat: os.stat_result,
    extra_ignores: tuple[str, ...],
    diagnostics: list[Diagnostic],
    state: _TraversalState,
) -> Iterator[_FileCandidate]:
    if state.exhausted:
        return
    if not _windows_directory_is_within_anchor(directory, root_anchor):
        _add_diagnostic(
            diagnostics, "root_escape", _display_path(relative_directory)
        )
        return
    before = _lstat_directory(
        directory, _display_path(relative_directory), diagnostics
    )
    if before is None:
        return
    if not _same_file(before, expected_stat):
        _add_diagnostic(
            diagnostics, "changed_directory", _display_path(relative_directory)
        )
        return
    try:
        with os.scandir(directory) as entries:
            ordered_entries = _bounded_sorted_entries(
                entries, relative_directory, diagnostics, state
            )
    except PermissionError:
        _add_diagnostic(
            diagnostics, "permission_denied", _display_path(relative_directory)
        )
        return
    except FileNotFoundError:
        _add_diagnostic(
            diagnostics, "disappeared_directory", _display_path(relative_directory)
        )
        return
    except OSError:
        _add_diagnostic(
            diagnostics, "unreadable_directory", _display_path(relative_directory)
        )
        return
    if ordered_entries is None:
        return
    after = _lstat_directory(directory, _display_path(relative_directory), diagnostics)
    if after is None:
        return
    if not _windows_directory_is_within_anchor(directory, root_anchor):
        _add_diagnostic(
            diagnostics, "root_escape", _display_path(relative_directory)
        )
        return
    if not _same_file(after, expected_stat):
        _add_diagnostic(
            diagnostics, "changed_directory", _display_path(relative_directory)
        )
        return

    for entry in ordered_entries:
        if state.exhausted:
            return
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
        if _is_reparse_point(entry_stat):
            _add_diagnostic(diagnostics, "reparse_point_skipped", relative_path)
        elif stat.S_ISDIR(entry_stat.st_mode):
            child_path = Path(entry.path)
            child_stat = _lstat_directory(child_path, relative_path, diagnostics)
            if child_stat is not None:
                yield from _walk_files_windows(
                    child_path,
                    root_anchor,
                    relative,
                    child_stat,
                    extra_ignores,
                    diagnostics,
                    state,
                )
        elif stat.S_ISREG(entry_stat.st_mode):
            file_path = Path(entry.path)
            try:
                file_stat = os.stat(file_path, follow_symlinks=False)
            except FileNotFoundError:
                _add_diagnostic(diagnostics, "disappeared_file", relative_path)
                continue
            except PermissionError:
                _add_diagnostic(diagnostics, "permission_denied", relative_path)
                continue
            except OSError:
                _add_diagnostic(diagnostics, "unreadable_file", relative_path)
                continue
            if _is_reparse_point(file_stat) or not stat.S_ISREG(file_stat.st_mode):
                _add_diagnostic(diagnostics, "changed_file", relative_path)
                continue
            yield _FileCandidate(relative_path, file_stat, disk_path=file_path)
        else:
            _add_diagnostic(diagnostics, "special_file", relative_path)


def _bounded_sorted_entries(
    entries: Iterator[os.DirEntry[str]],
    relative_directory: PurePosixPath,
    diagnostics: list[Diagnostic],
    state: _TraversalState,
) -> list[os.DirEntry[str]] | None:
    collected: list[os.DirEntry[str]] = []
    for entry in entries:
        if len(collected) >= state.max_directory_entries:
            state.exhausted = True
            _add_diagnostic(
                diagnostics, "max_files", _display_path(relative_directory)
            )
            return None
        collected.append(entry)
    return sorted(collected, key=lambda entry: entry.name)


def _read_regular_file(
    candidate: _FileCandidate,
    max_file_bytes: int,
    remaining_bytes: int,
    root_anchor: _WindowsRootAnchor | None,
    diagnostics: list[Diagnostic],
) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if candidate.parent_fd is not None and candidate.name is not None:
        try:
            descriptor = os.open(
                candidate.name, flags | nofollow, dir_fd=candidate.parent_fd
            )
        except FileNotFoundError:
            _add_diagnostic(diagnostics, "disappeared_file", candidate.path)
            return None
        except PermissionError:
            _add_diagnostic(diagnostics, "permission_denied", candidate.path)
            return None
        except OSError:
            _add_diagnostic(diagnostics, "unreadable_file", candidate.path)
            return None
    elif candidate.disk_path is not None:
        try:
            descriptor = os.open(candidate.disk_path, flags | nofollow)
        except FileNotFoundError:
            _add_diagnostic(diagnostics, "disappeared_file", candidate.path)
            return None
        except PermissionError:
            _add_diagnostic(diagnostics, "permission_denied", candidate.path)
            return None
        except OSError:
            _add_diagnostic(diagnostics, "unreadable_file", candidate.path)
            return None
    else:
        _add_diagnostic(diagnostics, "unsafe_file_primitive", candidate.path)
        return None

    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or not _same_file(
            candidate.expected_stat, opened_stat
        ):
            _add_diagnostic(diagnostics, "changed_file", candidate.path)
            return None
        if opened_stat.st_size > max_file_bytes:
            _add_diagnostic(diagnostics, "max_file_bytes", candidate.path)
            return None
        if opened_stat.st_size > remaining_bytes:
            _add_diagnostic(diagnostics, "max_total_bytes", candidate.path)
            return None
        if os.name == "nt" and (
            root_anchor is None
            or not _windows_handle_is_within_anchor(descriptor, root_anchor)
        ):
            _add_diagnostic(diagnostics, "root_escape", candidate.path)
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            return source.read(opened_stat.st_size)
    except OSError:
        _add_diagnostic(diagnostics, "unreadable_file", candidate.path)
        return None
    finally:
        os.close(descriptor)


def _lstat_directory(
    path: Path, relative_path: str, diagnostics: list[Diagnostic]
) -> os.stat_result | None:
    try:
        entry_stat = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        _add_diagnostic(diagnostics, "disappeared_directory", relative_path)
        return None
    except PermissionError:
        _add_diagnostic(diagnostics, "permission_denied", relative_path)
        return None
    except OSError:
        _add_diagnostic(diagnostics, "unreadable_directory", relative_path)
        return None
    if _is_reparse_point(entry_stat) or not stat.S_ISDIR(entry_stat.st_mode):
        _add_diagnostic(diagnostics, "symlink_skipped", relative_path)
        return None
    return entry_stat


def _is_reparse_point(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _open_windows_root_anchor(root: Path) -> _WindowsRootAnchor | None:
    if os.name != "nt":
        return None
    handle = _open_windows_path_handle(root, directory=True)
    if handle is None:
        return None
    final_path = _windows_final_path(handle)
    if final_path is None:
        _close_windows_handle(handle)
        return None
    return _WindowsRootAnchor(handle, _normalise_windows_path(final_path))


def _windows_directory_is_within_anchor(
    directory: Path, anchor: _WindowsRootAnchor
) -> bool:
    handle = _open_windows_path_handle(directory, directory=True)
    if handle is None:
        return False
    try:
        final_path = _windows_final_path(handle)
        return final_path is not None and _windows_path_is_within_anchor(
            final_path, anchor
        )
    finally:
        _close_windows_handle(handle)


def _windows_handle_is_within_anchor(
    descriptor: int, anchor: _WindowsRootAnchor
) -> bool:
    """Validate an opened source handle against the immutable root anchor."""
    if os.name != "nt":
        return True
    import msvcrt

    final_path = _windows_final_path(int(msvcrt.get_osfhandle(descriptor)))
    return final_path is not None and _windows_path_is_within_anchor(
        final_path, anchor
    )


def _open_windows_path_handle(path: Path, *, directory: bool) -> int | None:
    """Open a metadata handle without allowing deletion of the named object."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80,  # FILE_READ_ATTRIBUTES
        0x1 | 0x2,  # FILE_SHARE_READ | FILE_SHARE_WRITE (not FILE_SHARE_DELETE)
        None,
        3,  # OPEN_EXISTING
        0x02000000 if directory else 0,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid:
        return None
    return int(handle)


def _windows_final_path(handle: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32_768)
    length = get_final_path(handle, buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        return None
    return buffer.value


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _windows_path_is_within_anchor(
    final_path: str, anchor: _WindowsRootAnchor
) -> bool:
    try:
        return os.path.commonpath(
            (_normalise_windows_path(final_path), anchor.final_path)
        ) == anchor.final_path
    except ValueError:
        return False


def _normalise_windows_path(path: str) -> str:
    return os.path.normcase(path.removeprefix("\\\\?\\"))


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
