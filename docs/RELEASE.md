# Release process

Releases are built from a clean, tagged commit on `main` and published as
GitHub releases. Rootweft is not published to PyPI.

## Versioning

- Python package versions follow PEP 440 (`0.1.0a1`).
- Git tags and release names use a human-readable form (`v0.1.0-alpha`).
  `scripts/verify_release.py` maps one to the other and refuses a mismatch
  (`-alpha` → `a1`, `-beta.N` → `bN`, `-rc.N` → `rcN`).

## Artifacts

| File | Contents |
| --- | --- |
| `rootweft-<version>-py3-none-any.whl` | Wheel with `LICENSE` and `NOTICE` |
| `rootweft-<version>.tar.gz` | Source distribution including docs, tests, examples and scripts |
| `rootweft-<tag>.cdx.json` | CycloneDX 1.6 SBOM of the wheel plus its resolved runtime and `mcp`-extra dependencies (scope `required`/`optional`) |
| `SHA256SUMS` | Sorted SHA-256 checksums of the three files above |

The release workflow also publishes a GitHub build-provenance attestation for
the wheel and sdist.

Verify a download:

```bash
sha256sum -c SHA256SUMS                     # Linux
shasum -a 256 -c SHA256SUMS                 # macOS
gh attestation verify rootweft-0.1.0a1-py3-none-any.whl --repo edodelo/rootweft
```

On Windows PowerShell, compare `Get-FileHash -Algorithm SHA256 <file>` with the
matching line.

## Local quality gate

Run from a clean checkout with the development tools from
`requirements-dev.txt` and the package installed with `pip install -e ".[mcp]"`:

```bash
ruff check .
ruff format --check .
mypy src/rootweft
python -m pytest --cov=rootweft --cov-report=term-missing --cov-fail-under=80
python scripts/build_release.py
python scripts/verify_release.py
pip-audit
```

`build_release.py` refuses a dirty worktree, sets `SOURCE_DATE_EPOCH` to the
commit time, builds the wheel and sdist, installs the wheel with the `mcp`
extra into a fresh virtual environment to generate the SBOM from what was
actually installed, and writes `SHA256SUMS`.

`verify_release.py` checks the worktree is clean, the tag matches the version,
the file set is exact, every checksum matches, wheel/sdist metadata and
contents are complete, the SBOM is well formed, all public documents exist
with working local links, and that the wheel installs into a clean virtual
environment where `rootweft --version` and a sample build succeed.

## Continuous integration

`.github/workflows/`:

- `ci.yml` — lint, format, type check and tests with coverage on Ubuntu,
  macOS and Windows for Python 3.11, 3.12 and 3.13, plus a packaging job that
  builds and verifies release artifacts.
- `security.yml` — `pip-audit` of the resolved dependencies and a `gitleaks`
  secret scan of the full history, on pushes, pull requests and weekly.
- `release.yml` — on a `v*` tag: rebuild, verify, attest and create the
  GitHub release with the four artifacts.

All third-party actions are pinned to full commit SHAs. Workflows default to
`permissions: contents: read`; only the release job gets `contents: write`,
`id-token: write` and `attestations: write`. `pull_request_target` is never
used. Normal CI makes no remote model calls.

## Cutting a release

1. Update `CHANGELOG.md` and the version in `pyproject.toml` and
   `src/rootweft/__init__.py`.
2. Run the local quality gate; commit; push `main`; wait for green CI.
3. `git tag -a vX.Y.Z[-stage] -m "Rootweft vX.Y.Z[-stage]"` and
   `git push origin vX.Y.Z[-stage]`.
4. The release workflow publishes the artifacts. Download them and run
   `sha256sum -c SHA256SUMS`.
5. State in the release notes whether the live Jev smoke test was run.
