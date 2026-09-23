# Changelog

All notable changes to Rootweft are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Python package
versions follow PEP 440; Git tags use the human-readable form shown in each
heading.

## [Unreleased]

## [v0.1.0-alpha] — 2026-09-23 (package version 0.1.0a1)

First public alpha.

### Added

- Safe recursive scanner with default exclusions (VCS, environment and
  credential files, keys, vendor/dependency trees, caches, build output),
  no symlink following, race checks on POSIX and Windows, and resource limits
  that fail closed.
- Deterministic extraction for Python and stub files (`ast`), JavaScript,
  JSX, TypeScript and TSX (pinned official tree-sitter grammars) and Markdown
  headings with exact symbol mentions.
- Conservative resolver: parser-proven and exactly bound relationships become
  accepted edges; ambiguous references become bounded adjudication candidates;
  dynamic ones stay unresolved.
- Canonical, byte-stable `rootweft.graph.v1` JSON with stable SHA-256 IDs,
  relative POSIX evidence, schema versioning and atomic writes.
- CLI: `build`, `search`, `node`, `neighbors`, `path`, `explain`, `stats`,
  `export-html`, `provider-check`, `mcp`, `install-skill`, `uninstall-skill`,
  `mcp-config`, `--version`; JSON on stdout, diagnostics on stderr, documented
  exit codes.
- Self-contained offline HTML viewer with strict CSP and hostile-content
  handling.
- Optional review-only Jev adjudication through TypeSafe (`jev-1.13.0`) or an
  experimental OpenRouter route, with egress preview, budgets, secret checks
  and full provenance.
- Read-only MCP stdio server (optional `rootweft[mcp]` extra) with six bounded
  tools.
- Portable Agent Skill with a manifest-based installer for `.agents`,
  `.claude`, `.hermes` and `.cursor` skill directories.
- Security, privacy, data-egress, threat-model and provenance documentation;
  CI on Linux, macOS and Windows for Python 3.11–3.13; release checksums and
  CycloneDX SBOM.

### Known limitations

- Alpha: the graph schema and CLI may change before 1.0.
- Cross-file call resolution is intentionally conservative; for example a
  call to a name imported with `from module import name` is not yet linked to
  its definition.
- The Jev adapters are contract-tested against published API documentation
  but **not live-verified**; remote results are proposals for human review
  only, with no promotion policy or cache.
- Harness compatibility is limited to the checks recorded in
  `docs/HARNESS_COMPATIBILITY.md`.
- Large graphs are not rendered all at once by the viewer; it caps visible
  nodes and relationships.

[Unreleased]: https://github.com/edodelo/rootweft/compare/v0.1.0-alpha...HEAD
[v0.1.0-alpha]: https://github.com/edodelo/rootweft/releases/tag/v0.1.0-alpha
