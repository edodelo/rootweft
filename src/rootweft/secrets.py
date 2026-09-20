"""Conservative, content-redacting secret checks for remote egress."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from rootweft.scanner import ScannedFile

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pem_private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "api_token",
        re.compile(
            r"(?:\bAKIA[0-9A-Z]{16}\b|\bAIza[0-9A-Za-z_-]{35}\b|"
            r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}\b|"
            r"\bsk-[A-Za-z0-9_-]{8,}\b)"
        ),
    ),
    (
        "secret_assignment",
        re.compile(
            r"(?im)(?:^|[,{]\s*)[\"']?(?:client_secret|database_password|"
            r"aws_secret_access_key|password|passwd|pwd|secret)[\"']?\s*(?:=|:)\s*"
            r"(?:[\"'][^\"'\r\n]+[\"']|[^\s#,}\]]+)"
        ),
    ),
)
_HIGH_RISK_NAMES = frozenset(
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
_HIGH_RISK_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


@dataclass(frozen=True)
class SecretFinding:
    """A category-only secret signal, optionally located for egress reporting."""

    category: str
    path: str | None = None

    def __post_init__(self) -> None:
        if not self.category:
            raise ValueError("secret category must not be empty")
        if self.path is not None and (
            not self.path
            or "\\" in self.path
            or PurePosixPath(self.path).is_absolute()
            or any(part in {".", ".."} for part in PurePosixPath(self.path).parts)
        ):
            raise ValueError("secret path must be a relative POSIX path")


class SecretEgressError(ValueError):
    """Raised before a suspicious source snippet can leave the machine."""

    def __init__(self, findings: tuple[SecretFinding, ...]) -> None:
        self.findings = findings
        locations = ", ".join(
            f"{finding.category} at {finding.path}"
            for finding in findings
            if finding.path is not None
        )
        super().__init__(f"egress blocked by secret signals: {locations}")


def find_secrets(text: str) -> tuple[SecretFinding, ...]:
    """Classify established secret signatures without retaining matched text."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    return tuple(
        SecretFinding(category)
        for category, pattern in _SECRET_PATTERNS
        if pattern.search(text) is not None
    )


def ensure_safe_for_egress(files: Iterable[ScannedFile]) -> None:
    """Raise a content-redacting error if any file is unsafe to send remotely."""
    findings: set[tuple[str, str]] = set()
    for file in files:
        if not isinstance(file, ScannedFile):
            raise TypeError("files must contain ScannedFile instances")
        if _is_high_risk_filename(file.path):
            findings.add(("credential_filename", file.path))
        findings.update(
            (finding.category, file.path) for finding in find_secrets(file.text)
        )
    if findings:
        report = tuple(
            SecretFinding(category=category, path=path)
            for category, path in sorted(findings, key=lambda item: (item[1], item[0]))
        )
        raise SecretEgressError(report)


def _is_high_risk_filename(path: str) -> bool:
    name = PurePosixPath(path).name.casefold()
    return name in _HIGH_RISK_NAMES or name.endswith(_HIGH_RISK_SUFFIXES)
