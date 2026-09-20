"""Behavioural safety tests for repository scanning."""

from __future__ import annotations

import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

import rootweft.scanner as scanner
from rootweft.scanner import ScanLimits, scan_repository


def write(path: Path, content: str | bytes) -> None:
    """Create a controlled repository fixture file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        with path.open("w", encoding="utf-8", newline="\n") as fixture:
            fixture.write(content)


def make_symlink_or_skip(link: Path, target: Path) -> None:
    """Create a link where Windows permissions permit it."""
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable for this test user: {error.winerror}")


def make_junction_or_skip(link: Path, target: Path) -> None:
    """Create a Windows junction where the operating system permits it."""
    if os.name != "nt":
        pytest.skip("junctions are a Windows reparse-point feature")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("junction creation unavailable for this test user")


def test_scanner_excludes_credentials_and_vendor(tmp_path: Path) -> None:
    """Catches default exclusions allowing credential or vendor tree source in."""
    write(tmp_path / "src/app.py", "print('ok')")
    write(tmp_path / ".env", "TOKEN=fixture-secret")
    write(tmp_path / "node_modules/pkg/index.js", "bad()")

    result = scan_repository(tmp_path, ScanLimits())

    assert [item.path for item in result.files] == ["src/app.py"]


def test_scanner_excludes_environment_file_variants(tmp_path: Path) -> None:
    """Catches a default policy that reads a common environment-file variant."""
    write(tmp_path / "src/app.py", "print('ok')")
    write(tmp_path / ".env.production", "TOKEN=fixture-secret")

    result = scan_repository(tmp_path, ScanLimits())

    assert [item.path for item in result.files] == ["src/app.py"]


def test_symlink_escape_is_not_followed(tmp_path: Path) -> None:
    """Catches scanning a regular file by following a link outside the root."""
    outside = tmp_path.parent / "outside-rootweft-scanner.py"
    outside.write_text("secret = 1", encoding="utf-8")
    make_symlink_or_skip(tmp_path / "escape.py", outside)

    assert scan_repository(tmp_path, ScanLimits()).files == ()


def test_broken_symlink_is_skipped_with_a_diagnostic(tmp_path: Path) -> None:
    """Catches a broken link aborting scanning or being treated as source."""
    make_symlink_or_skip(tmp_path / "broken.py", tmp_path / "missing.py")

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["symlink_skipped"]


def test_directory_symlink_is_not_traversed(tmp_path: Path) -> None:
    """Catches recursive traversal following a directory link into another tree."""
    outside = tmp_path.parent / "outside-rootweft-directory-link"
    write(outside / "external.py", "outside = True\n")
    make_symlink_or_skip(tmp_path / "linked", outside)

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["symlink_skipped"]


def test_windows_junction_is_not_traversed(tmp_path: Path) -> None:
    """Catches a Windows directory junction bypassing symlink-only filtering."""
    outside = tmp_path.parent / "outside-rootweft-junction"
    write(outside / "external.py", "outside = True\n")
    make_junction_or_skip(tmp_path / "linked-junction", outside)

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["reparse_point_skipped"]


def test_scanner_returns_sorted_posix_paths_and_decodes_utf8_bom(
    tmp_path: Path,
) -> None:
    """Catches platform paths, unstable ordering, or leaked BOM text."""
    write(tmp_path / "z.py", "z = 1\n")
    bom_source = b"\xef\xbb\xbfanswer = 42\n"
    write(tmp_path / "nested/a.py", bom_source)

    result = scan_repository(tmp_path, ScanLimits())

    assert [(item.path, item.text, item.sha256) for item in result.files] == [
        ("nested/a.py", "answer = 42\n", sha256(bom_source).hexdigest()),
        ("z.py", "z = 1\n", sha256(b"z = 1\n").hexdigest()),
    ]


def test_scanner_reports_binary_input_without_source_content(tmp_path: Path) -> None:
    """Catches binary input being parsed or diagnostics echoing its contents."""
    marker = "fixture-secret-must-not-leak"
    write(tmp_path / "payload.bin", marker.encode("utf-8") + b"\x00")

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["binary_file"]
    assert marker not in " ".join(
        diagnostic.message for diagnostic in result.diagnostics
    )


def test_scanner_stops_at_file_limit_with_a_redacted_diagnostic(tmp_path: Path) -> None:
    """Catches the resource limit being ignored or leaking skipped source text."""
    marker = "fixture-secret-must-not-leak"
    write(tmp_path / "a.py", "a = 1\n")
    write(tmp_path / "b.py", f"b = '{marker}'\n")

    result = scan_repository(tmp_path, ScanLimits(max_files=1))

    assert [item.path for item in result.files] == ["a.py"]
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["max_files"]
    assert marker not in " ".join(
        diagnostic.message for diagnostic in result.diagnostics
    )


def test_scanner_honors_extra_ignore_patterns(tmp_path: Path) -> None:
    """Catches caller-provided ignores being silently ignored."""
    write(tmp_path / "src/keep.py", "keep = True\n")
    write(tmp_path / "src/skip.py", "skip = True\n")

    result = scan_repository(tmp_path, ScanLimits(), extra_ignores=("src/skip.py",))

    assert [item.path for item in result.files] == ["src/keep.py"]


def test_scanner_rejects_directory_replaced_by_an_outside_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches traversal reading a queued directory after it becomes a link."""
    outside = tmp_path.parent / "outside-rootweft-directory"
    write(outside / "external.py", "outside = True\n")
    queued = tmp_path / "queued"
    write(queued / "inside.py", "inside = True\n")
    original_scandir = scanner.os.scandir
    replaced = False

    def replace_before_scan(path: str | Path) -> os.ScandirIterator[str]:
        nonlocal replaced
        if Path(path).name == "queued" and not replaced:
            replaced = True
            for child in queued.iterdir():
                child.unlink()
            queued.rmdir()
            make_symlink_or_skip(queued, outside)
        return original_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", replace_before_scan)

    result = scan_repository(tmp_path, ScanLimits())

    assert [item.path for item in result.files] == []
    assert "queued/external.py" not in [item.path for item in result.files]
    assert "symlink_skipped" in [item.code for item in result.diagnostics]


def test_scanner_counts_rejected_candidates_against_file_limit(tmp_path: Path) -> None:
    """Catches max_files counting only sources accepted after decoding."""
    write(tmp_path / "a.bin", b"binary\x00")
    write(tmp_path / "b.py", "accepted = True\n")

    result = scan_repository(tmp_path, ScanLimits(max_files=1))

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["binary_file", "max_files"]


def test_scanner_charges_binary_bytes_against_total_budget(tmp_path: Path) -> None:
    """Catches binary reads escaping max_total_bytes accounting."""
    write(tmp_path / "a.bin", b"abc\x00")
    write(tmp_path / "b.py", "ok\n")

    result = scan_repository(tmp_path, ScanLimits(max_total_bytes=4))

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == [
        "binary_file",
        "max_total_bytes",
    ]


def test_scanner_reports_file_size_limit_before_reading(tmp_path: Path) -> None:
    """Catches an oversized candidate being decoded despite max_file_bytes."""
    write(tmp_path / "too-large.py", "large\n")

    result = scan_repository(tmp_path, ScanLimits(max_file_bytes=5))

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["max_file_bytes"]


def test_scanner_reports_invalid_utf8_without_echoing_input(tmp_path: Path) -> None:
    """Catches malformed text entering the scan result or its diagnostics."""
    marker = "fixture-secret-must-not-leak"
    write(tmp_path / "invalid.py", marker.encode("ascii") + b"\xff")

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["invalid_encoding"]
    assert marker not in " ".join(item.message for item in result.diagnostics)


def test_scanner_reports_permission_denied_from_directory_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a permission error aborting a scan instead of producing a result."""
    write(tmp_path / "blocked/app.py", "blocked = True\n")
    original_scandir = scanner.os.scandir

    def deny_blocked(path: str | Path) -> os.ScandirIterator[str]:
        if Path(path).name == "blocked":
            raise PermissionError("test boundary")
        return original_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", deny_blocked)

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["permission_denied"]


def test_scanner_reports_disappearing_directory_from_os_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a vanished queued directory aborting a scan."""
    write(tmp_path / "gone/app.py", "gone = True\n")
    original_scandir = scanner.os.scandir

    def remove_before_scan(path: str | Path) -> os.ScandirIterator[str]:
        if Path(path).name == "gone":
            raise FileNotFoundError("test boundary")
        return original_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", remove_before_scan)

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["disappeared_directory"]


def test_scanner_reports_file_that_disappears_at_open_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a vanished candidate aborting the rest of the real scanner run."""
    write(tmp_path / "gone.py", "gone = True\n")
    original_open = scanner.os.open

    def remove_at_open(
        path: str | Path, flags: int, *args: object, **kwargs: int
    ) -> int:
        if Path(path).name == "gone.py":
            raise FileNotFoundError("test boundary")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(scanner.os, "open", remove_at_open)

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["disappeared_file"]


@pytest.mark.skipif(os.name == "nt", reason="Windows has no portable FIFO fixture")
def test_scanner_skips_special_fifo(tmp_path: Path) -> None:
    """Catches a special device entering source decoding as if it were a file."""
    fifo = tmp_path / "stream"
    os.mkfifo(fifo)

    result = scan_repository(tmp_path, ScanLimits())

    assert result.files == ()
    assert [item.code for item in result.diagnostics] == ["special_file"]
