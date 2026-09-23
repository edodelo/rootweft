# Third-party notices

Rootweft's wheel and source distribution contain only Rootweft's own code.
The packages below are installed separately by your package manager when you
install Rootweft; they are listed here so you can review their licenses. The
versions are those resolved for the `v0.1.0-alpha` release on 2026-09-23; the
release SBOM (`rootweft-0.1.0-alpha.cdx.json`) is the authoritative list for
that build.

## Runtime dependencies (always installed)

| Package | Version | License | Why |
| --- | --- | --- | --- |
| httpx | 0.28.1 (pinned) | BSD-3-Clause | HTTPS client for optional provider calls |
| tree-sitter | 0.25.2 (pinned) | MIT | Parser runtime |
| tree-sitter-javascript | 0.25.0 (pinned) | MIT | Official JavaScript/JSX grammar |
| tree-sitter-typescript | 0.23.2 (pinned) | MIT | Official TypeScript/TSX grammar |
| httpcore | 1.0.9 | BSD-3-Clause | httpx dependency |
| h11 | 0.16.0 | MIT | httpx dependency |
| anyio | 4.15.1 | MIT | httpx dependency |
| idna | 3.20 | BSD-3-Clause | httpx dependency |
| certifi | 2026.7.22 | MPL-2.0 | CA bundle used by httpx |
| typing_extensions | 4.16.0 | PSF-2.0 | Typing backports |

## Optional `mcp` extra

| Package | Version | License |
| --- | --- | --- |
| mcp | 2.2.0 (pinned) | MIT |
| mcp-types | 2.2.0 | MIT |
| pydantic / pydantic_core | 2.13.5 / 2.46.5 | MIT |
| annotated-types, typing-inspection | 0.8.0, 0.4.4 | MIT |
| jsonschema, jsonschema-specifications, referencing, rpds-py, attrs | 4.26.0, 2025.9.1, 0.37.0, 2026.6.3, 26.1.0 | MIT |
| starlette, sse-starlette, uvicorn | 1.7.0, 3.4.11, 0.53.0 | BSD-3-Clause |
| httpx2, httpcore2 | 2.13.1, 2.13.1 | BSD-3-Clause |
| click | 8.5.0 | BSD-3-Clause |
| PyJWT | 2.15.0 | MIT |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| cffi, pycparser | 2.1.1, 3.0 | MIT-0, BSD-3-Clause |
| opentelemetry-api | 1.44.0 | Apache-2.0 |
| python-multipart | 0.0.32 | Apache-2.0 |
| truststore | 0.10.4 | MIT |
| pywin32 (Windows only) | ≥311 | PSF-2.0 |

Transitive versions float within the ranges their parents allow; run
`pip list` in your environment to see what you actually installed.

## Development-only tools

pytest, pytest-cov, ruff, mypy, build and pip-audit are used to develop and
release Rootweft and are not installed with it.

## Services

TypeSafe AI (Jev) and OpenRouter are third-party services used only when you
explicitly enable remote adjudication. Their terms are between you and them.
