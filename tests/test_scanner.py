"""Behavioural safety tests for repository scanning."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

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
