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
    credential = ScannedFile(
        "config/credentials.json", "json", "{}\n", "c" * 64
    )

    with pytest.raises(SecretEgressError) as raised:
        ensure_safe_for_egress((credential,))

    assert "credential_filename" in str(raised.value)
    assert "config/credentials.json" in str(raised.value)


def test_egress_guard_allows_clean_scanned_files() -> None:
    """Catches a guard that rejects ordinary non-secret source code."""
    clean = ScannedFile("src/clean.py", "python", "answer = 42\n", "d" * 64)

    ensure_safe_for_egress((clean,))
