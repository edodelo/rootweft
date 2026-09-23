"""Behavioural tests for the last secret check before remote egress."""

from __future__ import annotations

import pytest

from rootweft.scanner import ScannedFile
from rootweft.secrets import SecretEgressError, ensure_safe_for_egress, find_secrets


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("-----BEGIN PRIVATE KEY-----\nfixture\n", "pem_private_key"),
        ("token = 'sk-fixture-token-value'\n", "api_token"),
        ("password = 'fixture-password'\n", "secret_assignment"),
    ],
)
def test_find_secrets_classifies_conservative_secret_signals(
    text: str, category: str
) -> None:
    """Catches an egress guard that misses an established secret signal."""
    findings = find_secrets(text)

    assert [finding.category for finding in findings] == [category]


def test_egress_guard_blocks_secret_files_without_echoing_secret_text() -> None:
    """Catches secret-bearing content reaching an egress caller or being echoed."""
    marker = "fixture-secret-must-not-leak"
    files = (
        ScannedFile("src/clean.py", "python", "answer = 42\n", "a" * 64),
        ScannedFile("src/config.py", "python", f"password = '{marker}'\n", "b" * 64),
    )

    with pytest.raises(SecretEgressError) as raised:
        ensure_safe_for_egress(files)

    report = str(raised.value)
    assert "secret_assignment" in report
    assert "src/config.py" in report
    assert marker not in report


def test_egress_guard_blocks_high_risk_credential_filename() -> None:
    """Catches an egress path that relies only on text matching for credentials."""
    credential = ScannedFile("config/credentials.json", "json", "{}\n", "c" * 64)

    with pytest.raises(SecretEgressError) as raised:
        ensure_safe_for_egress((credential,))

    assert "credential_filename" in str(raised.value)
    assert "config/credentials.json" in str(raised.value)


def test_egress_guard_allows_clean_scanned_files() -> None:
    """Catches a guard that rejects ordinary non-secret source code."""
    clean = ScannedFile("src/clean.py", "python", "answer = 42\n", "d" * 64)

    ensure_safe_for_egress((clean,))


@pytest.mark.parametrize(
    "text",
    [
        'CLIENT_SECRET = "fixture-value"\n',
        'DATABASE_PASSWORD: "fixture-value"\n',
        'aws_secret_access_key = "fixture-value"\n',
        '{"client_secret": "fixture-value"}\n',
    ],
)
def test_find_secrets_catches_common_named_assignments(text: str) -> None:
    """Catches a named credential assignment missing the egress guard."""
    assert [item.category for item in find_secrets(text)] == ["secret_assignment"]


@pytest.mark.parametrize("path", [".npmrc", ".pypirc", ".netrc", ".git-credentials"])
def test_egress_guard_blocks_common_credential_filenames(path: str) -> None:
    """Catches a credential filename bypassing text-only secret detection."""
    file = ScannedFile(path, "text", "registry = example\n", "e" * 64)

    with pytest.raises(SecretEgressError) as raised:
        ensure_safe_for_egress((file,))

    assert "credential_filename" in str(raised.value)
    assert path in str(raised.value)


def test_find_secrets_allows_a_non_assignment_mention() -> None:
    """Catches a broad assignment pattern flagging ordinary documentation text."""
    findings = find_secrets('label = "CLIENT_SECRET is configured elsewhere"\n')

    assert findings == ()
