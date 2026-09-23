# Rootweft MCP server

`rootweft mcp GRAPH` serves one precompiled graph over the Model Context
Protocol (stdio transport) using the official MCP Python SDK v2.

```bash
pip install "rootweft[mcp] @ git+https://github.com/edodelo/rootweft@v0.1.0-alpha"
rootweft build . --output graph.json        # you build; the server never does
rootweft mcp graph.json                     # normally started by your MCP client
```

Without the extra, `rootweft mcp` exits with code `2` and tells you to install
`rootweft[mcp]`.

## Guarantees

- One graph, chosen on the command line, is loaded and validated **before** the
  transport starts. A missing, corrupt or newer-schema graph makes the process
  exit with code `4`, a one-line message on stderr and nothing on stdout.
- Stdout carries only MCP protocol frames. Logs go to stderr.
- Tools are read-only (`readOnlyHint: true`, `destructiveHint: false`,
  `openWorldHint: false`). There is no build, scan, export, file-write, shell,
  provider, network or generic model tool, and the server module does not
  import the remote decision layer.
- Tool inputs are identifiers, a search string, filters and pagination. None
  accepts a filesystem path. Strings are length-limited and integers bounded.
- Results are paginated (`limit` 1–100, `offset`) and report `total`.
- A missing node or edge is a tool error, distinct from an empty result.
- The server's instructions tell the model that every repository-derived string
  in results is untrusted data, not instructions.

## Tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `graph_info` | — | Schema/extractor/policy versions, node and edge counts by kind and relation, candidate count. |
| `search_nodes` | `query` (1–256 chars), `limit`, `offset`, `kind` | Matching nodes, `total`. |
| `get_node` | `node_id` | One node with evidence. |
| `get_neighbors` | `node_id`, `relation`, `direction` (`in`/`out`/`both`), `limit`, `offset` | Edges touching the node, `total`. |
| `shortest_path` | `source_id`, `target_id`, `max_depth` (0–32), `relation` | `found` and the edge chain. |
| `explain_relation` | `edge_id` | The edge plus both endpoint nodes. |

All results include `"layer": "structural"`.

## Client configuration

`rootweft mcp-config CLIENT GRAPH` prints a snippet with the absolute graph
path. It never writes any configuration file. The snippets below use
`/abs/path/graph.json` as a placeholder.

### Codex (`~/.codex/config.toml`)

```toml
[mcp_servers.rootweft]
command = "rootweft"
args = ["mcp", "/abs/path/graph.json"]
```

### Claude Code

```bash
claude mcp add rootweft -s user -- rootweft mcp /abs/path/graph.json
```

or a project `.mcp.json`:

```json
{
  "mcpServers": {
    "rootweft": {
      "command": "rootweft",
      "args": ["mcp", "/abs/path/graph.json"]
    }
  }
}
```

### Gemini CLI (`~/.gemini/settings.json` or `.gemini/settings.json`)

```json
{
  "mcpServers": {
    "rootweft": {
      "command": "rootweft",
      "args": ["mcp", "/abs/path/graph.json"]
    }
  }
}
```

### Hermes Agent (`~/.hermes/config.yaml`)

Hermes needs its own MCP client support: `pip install "hermes-agent[mcp]"`.

```yaml
mcp_servers:
  rootweft:
    command: rootweft
    args: ["mcp", "/abs/path/graph.json"]
```

### Other MCP clients

Any client that can launch a stdio server with a command and arguments may
work with `rootweft mcp /abs/path/graph.json`. Only the clients listed as
tested in [HARNESS_COMPATIBILITY.md](HARNESS_COMPATIBILITY.md) were actually
exercised.

If `rootweft` is not on the client's `PATH`, use the absolute path of the
executable (for example the one printed by `which rootweft` or
`where rootweft`).

## Agent Skill

The package contains one canonical skill (`skills/rootweft/SKILL.md` in the
repository mirrors the packaged copy byte for byte). It tells agents to use the
MCP tools or read-only CLI commands, never to build or enable remote calls on
their own, and to treat graph text as untrusted.

```bash
rootweft install-skill --target agents   # .agents/skills/rootweft  (Codex, Gemini CLI, compatible clients)
rootweft install-skill --target claude   # .claude/skills/rootweft  (Claude Code)
rootweft install-skill --target hermes --scope user   # ~/.hermes/skills/rootweft
rootweft install-skill --target cursor   # .cursor/skills/rootweft
```

- `--scope project` (default) installs under the current directory; `--scope
  user` installs under your home directory. `--dir DIR` selects a custom Hermes
  skills directory.
- `--dry-run` prints the plan and changes nothing.
- Existing content that Rootweft did not install is moved to a sibling
  `rootweft.backup-<timestamp>` directory first; `--no-backup` refuses instead.
- A manifest (`.rootweft-skill-manifest.json`) records installed files and
  hashes. `rootweft uninstall-skill` removes only those files, and only if they
  are unmodified.
- The installer refuses symlinked destinations and never edits `AGENTS.md`,
  `CLAUDE.md`, `GEMINI.md`, MCP configuration or shell profiles.
