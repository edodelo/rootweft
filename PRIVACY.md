# Privacy

Rootweft is a local command-line tool. It has **no telemetry**, no analytics,
no update checks and no hosted service. The project maintainers receive no
data from your use of it.

## Default (offline) operation

`build`, all query commands, `export-html`, `mcp`, `install-skill`,
`uninstall-skill` and `mcp-config` make no network requests. The test suite
intercepts socket creation to check this for the offline and dry-run paths.

What stays on your machine:

- **Graph files** contain file paths relative to the scanned root, symbol and
  heading names, qualified names, line ranges, SHA-256 fingerprints and graph
  metadata. They do not contain source text, but names and paths can still be
  confidential. Absolute paths are never stored.
- **Exported HTML** embeds the same graph data. Treat it like the graph.
- **Skill installation** writes a Markdown file and a manifest to the chosen
  skill directory.

Deleting these files removes everything Rootweft produced. Rootweft keeps no
other state, cache or log files.

## Optional remote adjudication

Only when you pass both `--provider NAME` and `--enable-remote`, Rootweft
sends bounded questions to that provider. What is sent, and how to preview it,
is described in [DATA_EGRESS.md](DATA_EGRESS.md).

- You supply your own API key through an environment variable. Rootweft never
  writes keys to disk and redacts them from errors.
- The provider's own privacy policy, retention and training terms apply to
  what you send. Rootweft cannot make guarantees about data once it reaches a
  third party, and retention by TypeSafe AI or OpenRouter was not verified for
  this release.
- To stop remote processing, simply do not pass the flags; nothing in
  configuration files or the environment can enable it.

## MCP and agent harnesses

The MCP server returns graph data to the MCP client that started it. What the
client or its model provider does with that data is governed by that client,
not by Rootweft. If your graph is confidential, only connect it to clients you
would also allow to read the repository.
