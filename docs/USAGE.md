# Using Rootweft

All examples below were run against `examples/sample_repo` from a checkout of
this repository. Output is abbreviated with `…` where noted.

## Build a graph

```bash
rootweft build examples/sample_repo --output graph.json
```

```json
{"candidate_count":1,"diagnostic_count":0,"edge_count":11,"edges_by_relation":{"calls":1,"contains":8,"imports":2},"layer":"structural","node_count":13,"nodes_by_kind":{"external":1,"file":4,"function":4,"heading":1,"module":3},"output":"graph.json"}
```

With `--json`, `build` prints the canonical graph itself (the same bytes it
wrote to `--output`) instead of the summary.

What is scanned:

- `.py` and `.pyi` (standard-library `ast`), `.js`, `.jsx`, `.mjs`, `.cjs`,
  `.ts`, `.tsx` (pinned tree-sitter grammars) and Markdown headings plus exact
  symbol mentions (disable with `--no-include-markdown`).
- Excluded by default: `.git` and other VCS data, environment files
  (`.env`, `.env.*`), private keys and common credential files, virtual
  environments, dependency/vendor trees such as `node_modules`, caches, build
  output and Rootweft's own output directory.
- Symlinks are not followed; special files, binary files and invalid UTF-8 are
  skipped with a diagnostic. A syntax error keeps the file node and records a
  diagnostic without inventing symbols.

Resource limits (all optional flags on `build`):

| Flag | Default |
| --- | --- |
| `--max-files` | 10000 |
| `--max-file-bytes` | 2000000 |
| `--max-total-bytes` | 100000000 |
| `--max-nodes` | 100000 |
| `--max-edges` | 200000 |
| `--max-candidates` | 20000 |
| `--max-diagnostics` | 1000 |
| `--max-candidate-options` | 16 |

A file larger than `--max-file-bytes` is skipped with a diagnostic. Exceeding
any other (global) limit fails the build with exit `3`, because a truncated
scan must not look like a complete graph; an existing graph file is left
untouched. Writes go to a temporary sibling that is atomically renamed into
place.

## Query a graph

Every query command takes the graph path first and prints one JSON object.

```bash
rootweft stats graph.json
rootweft search graph.json helper --limit 20 --offset 0 [--kind function]
rootweft node graph.json NODE_ID
rootweft neighbors graph.json NODE_ID [--direction in|out|both] [--relation calls] [--limit N] [--offset N]
rootweft path graph.json SOURCE_ID TARGET_ID [--max-depth 8] [--relation calls]
rootweft explain graph.json EDGE_ID
```

Example: find callers and callees.

```bash
rootweft search graph.json main
# {"layer":"structural","limit":20,"nodes":[{…"id":"8e13eb76…","kind":"function","name":"main","qualified_name":"app.main"…}],"offset":0,"total":1}

rootweft neighbors graph.json 8e13eb7629a41a9348c39455580198e3a165c8233c679b577ca64e0a82bdc555 --direction out --relation calls
# {"edges":[{"evidence":{"end_line":6,"path":"app.py","start_line":6},…,"origin":"resolver","relation":"calls","status":"accepted",…}],"layer":"structural",…,"total":1}
```

`limit` is 1–100. A missing node or edge is an error (exit `2`); an empty list
means nothing matched.

In the sample, `README.md` mentions `helper`, which exists in both `app.py`
and `lib.py`. Rootweft does not guess: the mention becomes one adjudication
candidate with both functions as options (`candidate_count: 1`).

## Offline HTML viewer

```bash
rootweft export-html graph.json --output graph.html [--max-visible 500]
```

The file is self-contained: no CDN, fonts or network requests, and a Content
Security Policy that blocks them. Repository strings are rendered as text
only. The viewer caps visible nodes and relationship expansion; narrow the
filters to inspect large graphs. `export-html` refuses to overwrite its own
source graph.

## Configuration

Precedence is: command-line flag, then `ROOTWEFT_*` environment variable, then
project file, then built-in default.

The project file is `.rootweft.toml` in the build root (for `build`) or in the
current directory (other commands). Keys may be top-level or under
`[rootweft]`:

```toml
[rootweft]
output = "graph.json"      # build: written to .rootweft/graph.json
include_markdown = true
max_files = 5000
html_output = "graph.html" # export-html
limit = 50
```

Environment variables use the upper-case key, for example
`ROOTWEFT_MAX_FILES=5000` or `ROOTWEFT_HTML_OUTPUT=map.html`.

Security rules for configuration:

- A repository's own `.rootweft.toml` can only direct `build` output into that
  repository's `.rootweft/` directory. Absolute paths, `..`, drive letters and
  links are rejected. An explicit `--output` flag is not restricted.
- Provider selection and remote permission are **never** read from files or
  environment variables. They must be typed on the command line each time.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 2 | Usage, configuration or query error (including missing node/edge) |
| 3 | Scan, build or output failure |
| 4 | Graph file unreadable, corrupt or from a newer schema |
| 5 | Remote adjudication failed and `--require-remote` was given |
| 70 | Unexpected internal error (details are redacted) |

## Agent integration commands

```bash
rootweft mcp graph.json                        # MCP stdio server (needs rootweft[mcp])
rootweft mcp-config codex|claude|gemini|hermes|generic graph.json
rootweft install-skill --target agents|claude|hermes|cursor [--scope project|user] [--dir DIR] [--dry-run] [--no-backup]
rootweft uninstall-skill --target … [--scope …] [--dir DIR] [--dry-run]
```

See [MCP.md](MCP.md) for details.

## Remote adjudication commands

```bash
rootweft build PATH --provider typesafe --enable-remote --dry-run-egress
rootweft build PATH --provider typesafe --enable-remote [--require-remote] --output graph.json
rootweft provider-check --provider typesafe --enable-remote
```

See [JEV.md](JEV.md) and [../DATA_EGRESS.md](../DATA_EGRESS.md).
