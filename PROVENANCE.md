# Provenance

## Independence

Rootweft is an independent implementation written for this repository. It is
not a fork of Graphify or of any other code-graph tool and does not include
their source code, tests, prompts, documentation, assets, user interface or
branding. It is not affiliated with, sponsored by or endorsed by Graphify
Labs, TypeSafe AI, OpenRouter, or the vendors of the agent harnesses it
documents. It makes no claim of feature equivalence with any other product.

Public descriptions of existing repository-graph tools were read as general
background on the problem space. The design, specification
(`docs/superpowers/specs/`), implementation plan (`docs/superpowers/plans/`)
and code were produced for Rootweft and are version-controlled here, so their
history can be inspected commit by commit.

## How the code was produced

Rootweft was developed with substantial assistance from AI coding agents,
directed and reviewed by the maintainer, following a written specification and
a test-first plan. Each task records its tests and review rounds in the Git
history. As with any code, contributors are responsible for reviewing what
they submit.

## External specifications consulted

Rootweft implements against public, documented interfaces. These documents
were consulted; no text was copied from them into the product.

| Topic | Source |
| --- | --- |
| Jev API (direct) | TypeSafe AI API and model documentation: <https://docs.typesafe.ai/api.md>, <https://docs.typesafe.ai/models.md> |
| Jev via OpenRouter | OpenRouter Decisions API reference and Jev guide: <https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request.md>, <https://openrouter.ai/docs/guides/community/jev-tutorial.md> |
| MCP | Model Context Protocol specification and the official Python SDK (`mcp` 2.2.0) |
| Agent Skills | Public Agent Skills `SKILL.md` convention (name/description front matter in a per-skill directory) |
| Parsing | Python `ast` documentation; tree-sitter and its official JavaScript/TypeScript grammars |
| Harness configuration | Public documentation and `--help` output of Codex CLI, Claude Code, Gemini CLI and Hermes Agent |

## Dependencies

Runtime and optional dependencies and their licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Each release also ships a
CycloneDX SBOM.
