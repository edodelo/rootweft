# Threat model

Scope: Rootweft `0.1.0a1` — scanner, extractors, resolver, graph
serialization, CLI, offline viewer, MCP server, skill installer and the
optional Jev adapters.

## Assets

1. The user's source code and secrets in or near the repository.
2. The integrity of existing graph files and of files outside Rootweft's
   documented outputs.
3. The user's agent harness: its configuration, instructions and the model's
   behaviour when reading graph data.
4. The user's provider credentials and spending.

## Trust boundaries and assumptions

- **The scanned repository is untrusted.** It may contain hostile file names,
  symlinks, junctions, special files, huge or binary files, malformed code,
  prompt-injection text and a hostile `.rootweft.toml`.
- **Graph files are untrusted input** when loaded (they may be edited or come
  from someone else).
- **Provider responses are untrusted.**
- **Trusted:** the local user who runs commands, the Python interpreter and
  installed dependencies, and the MCP client the user chose to connect.

## Threats and mitigations

| # | Threat | Mitigation | Evidence |
| --- | --- | --- | --- |
| T1 | Code execution while indexing | Parsers never import or execute repository code: Python via `ast`, JS/TS via tree-sitter, Markdown via a small deterministic parser. | Extractor tests |
| T2 | Reading outside the root (traversal, symlink or junction swap, races) | Canonical root; symlinks not followed; POSIX traversal by directory file descriptors with `O_NOFOLLOW`; Windows handle final-path checks against an immutable root anchor; changed-directory detection. | `tests/test_scanner.py` |
| T3 | Reading secrets | Default exclusions for VCS data, environment files, key and credential files, vendor/dependency trees, caches and build output. | Scanner tests |
| T4 | Resource exhaustion | Limits on files, bytes per file, total bytes, nodes, edges, candidates, diagnostics, options, graph import size, text length, nesting depth, query results, path search and viewer expansion. Global limits fail closed. | Scanner, pipeline, serialization, viewer tests |
| T5 | Silent data egress | Network only with `--provider` **and** `--enable-remote` on the command line; never from config/env. Fixed HTTPS origins, verified TLS, no redirects, no proxies from the environment, no cross-provider fallback. Dry-run preview opens no socket. | `tests/test_egress.py`, CLI tests |
| T6 | Leaking code or secrets to a provider | Only labels and coordinates are sent; excluded-path evidence refused; secret detector on every label and the whole body; size and count budgets. | Egress and provider tests |
| T7 | Malicious provider responses | Strict schema, duplicate-key rejection, finite and range checks, answer/question bijection, model identity check, bounded response size, compressed bodies refused. Results only enter the review overlay. | Decision model tests |
| T8 | Corrupting a good graph | Atomic temp-file-and-rename writes; failed builds and exports leave existing files untouched; `export-html` refuses to overwrite its source. | CLI and serialization tests |
| T9 | Hostile repository config redirecting output | Repository `.rootweft.toml` may only write under its own `.rootweft/` directory; absolute paths, `..`, drives and links rejected. | CLI tests |
| T10 | Script injection in the HTML viewer | Graph embedded as base64 data in an inert element; DOM built only with `textContent`; strict CSP with hashed inline script/style and `connect-src 'none'`; control and bidi characters stripped for display; long labels truncated. | `tests/test_viewer.py` |
| T11 | Prompt injection against agents via graph text | MCP instructions and the Agent Skill state that repository-derived strings are untrusted data. The server is read-only, so injected text cannot trigger builds, writes, network or shell through Rootweft. | MCP tests, skill text |
| T12 | MCP server abused as a capability | Six read-only tools over one graph fixed at startup; no path arguments; bounded schemas; no provider imports; stdout reserved for protocol frames. | `tests/test_mcp_server.py` |
| T13 | Skill installer damaging user files | Writes only to the documented skill directory; backup before replacing foreign content; manifest-based uninstall of unmodified files only; refuses symlinked destinations; never edits instruction or harness config files. | `tests/test_skill_installer.py` |
| T14 | Credential leakage | Keys read from the environment only at call time, never persisted, redacted from errors; provider error bodies not echoed. | Provider tests |
| T15 | Supply chain | Pinned runtime dependencies; official grammar wheels (no runtime grammar downloads); CI actions pinned to commit SHAs with least-privilege permissions; dependency audit and secret scanning in CI; release checksums and SBOM. | `.github/workflows`, `scripts/` |

## Residual risks

- Secret detection is heuristic; a novel token format in a symbol name could
  be sent when remote adjudication is enabled. Review `--dry-run-egress`.
- Graph files and HTML exports contain names and paths. They are as sensitive
  as your repository's structure.
- An MCP client's model can still be misled by injected text in names; the
  mitigation is advisory plus the lack of dangerous capabilities.
- Windows protections depend on `GetFinalPathNameByHandleW`; unusual
  filesystems or filter drivers were not tested.
- The Jev adapters were not exercised against the live services for this
  release.
