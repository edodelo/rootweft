# Rootweft

Rootweft builds a deterministic, evidence-backed **structural knowledge graph**
of a Python, JavaScript or TypeScript repository, then lets humans and coding
agents navigate it through a CLI, a read-only MCP server, a portable Agent
Skill and a self-contained offline HTML viewer.

- **Local and offline by default.** Parsing never imports or executes
  repository code, and no network request is made unless you pass both
  `--provider` and `--enable-remote`.
- **Deterministic.** The same tree and settings produce byte-identical JSON
  (`rootweft.graph.v1`), with stable SHA-256 identifiers and relative POSIX
  evidence paths.
- **Conservative.** Only parser-proven or exactly resolved relationships
  become accepted edges. Dynamic or ambiguous references stay unresolved or
  become bounded candidates for review; they are never guessed.
- **Optional, bounded adjudication.** With your own key, candidates can be
  sent to TypeSafe's hosted Jev model for review-only proposals recorded in a
  separate overlay. The structural layer is never modified by a model.

> **Status: v0.1.0-alpha.** Interfaces and the graph schema may change before
> 1.0. Remote adjudication is contract-tested against documented APIs but has
> not been live-verified. See [CHANGELOG.md](CHANGELOG.md) for known
> limitations.

Rootweft is an independent project. It is not affiliated with or endorsed by
Graphify Labs, TypeSafe AI, OpenRouter, or any agent-harness vendor. See
[PROVENANCE.md](PROVENANCE.md).

## Install

Rootweft needs Python 3.11, 3.12 or 3.13. It is distributed through GitHub
releases (it is not published on PyPI).

```bash
# CLI only
pipx install "git+https://github.com/edodelo/rootweft@v0.1.0-alpha"

# CLI plus the MCP server extra
pipx install "rootweft[mcp] @ git+https://github.com/edodelo/rootweft@v0.1.0-alpha"
```

You can also download the wheel from the
[release page](https://github.com/edodelo/rootweft/releases/tag/v0.1.0-alpha),
verify it against `SHA256SUMS`, and install it with
`pip install "./rootweft-0.1.0a1-py3-none-any.whl[mcp]"`.

## Quick start

```bash
git clone https://github.com/edodelo/rootweft
cd rootweft

rootweft build examples/sample_repo --output graph.json
rootweft stats graph.json
rootweft search graph.json helper
rootweft export-html graph.json --output graph.html   # open in any browser
```

Every query command prints exactly one JSON document on stdout; diagnostics go
to stderr. Exit codes: `0` success, `2` usage/configuration, `3` scan/build or
output failure, `4` unreadable or incompatible graph, `5` required remote
adjudication failed, `70` internal error.

See [docs/USAGE.md](docs/USAGE.md) for every command and option.

## Use it from a coding agent

1. Build the graph yourself: `rootweft build . --output graph.json`.
2. Connect the read-only MCP server (requires the `mcp` extra). Print a
   snippet for your client with `rootweft mcp-config CLIENT graph.json`, where
   `CLIENT` is `codex`, `claude`, `gemini`, `hermes` or `generic`.
3. Optionally install the Agent Skill that teaches agents how to use the
   graph safely:

   ```bash
   rootweft install-skill --target claude --dry-run   # show the plan
   rootweft install-skill --target claude             # .claude/skills/rootweft
   ```

The MCP server exposes six read-only tools (`graph_info`, `search_nodes`,
`get_node`, `get_neighbors`, `shortest_path`, `explain_relation`) over one
graph chosen at startup. It cannot build, scan, export, write files, run
commands or reach the network. Details: [docs/MCP.md](docs/MCP.md). Which
clients were actually exercised is recorded in
[docs/HARNESS_COMPATIBILITY.md](docs/HARNESS_COMPATIBILITY.md).

## What is in a graph

| Layer | Contents |
| --- | --- |
| `structural` | Nodes (`file`, `module`, `class`, `function`, `method`, `heading`, `external`) and edges (`contains`, `imports`, `calls`, `references`, `mentions`) with origin, status and line evidence. |
| `adjudication` | Bounded candidates for ambiguous references, plus optional remote decisions with full provenance. |

Source text is not stored in the graph; evidence is a relative path, a line
range and a fingerprint. Names and paths from your repository are still
present, so treat graph files and exported HTML as confidential if your code
is.

## Optional Jev adjudication

```bash
export TYPESAFE_API_KEY=...          # your own key, never stored
rootweft build . --provider typesafe --enable-remote --dry-run-egress  # preview only
rootweft build . --provider typesafe --enable-remote --output graph.json
```

Read [DATA_EGRESS.md](DATA_EGRESS.md) and [docs/JEV.md](docs/JEV.md) before
enabling it.

## Documentation

- [docs/USAGE.md](docs/USAGE.md) — commands, configuration, exit codes
- [docs/MCP.md](docs/MCP.md) — MCP server and client configuration
- [docs/HARNESS_COMPATIBILITY.md](docs/HARNESS_COMPATIBILITY.md) — tested clients
- [docs/JEV.md](docs/JEV.md) — optional remote adjudication
- [docs/RELEASE.md](docs/RELEASE.md) — how releases are built and verified
- [SECURITY.md](SECURITY.md), [THREAT_MODEL.md](THREAT_MODEL.md),
  [PRIVACY.md](PRIVACY.md), [DATA_EGRESS.md](DATA_EGRESS.md)
- [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## License

Apache-2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
