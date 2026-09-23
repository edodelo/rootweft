# Harness compatibility evidence

This page records what was actually executed. "Tested" means the listed check
ran and passed on the stated date; it does not mean every feature of that
client was exercised. Clients or checks not listed here are untested and *may*
work with any MCP stdio configuration.

Rootweft version for all rows: `0.1.0a1` (tag `v0.1.0-alpha`), MCP server on
official MCP Python SDK `2.2.0`, stdio transport.

## MCP server

| Client | Version | OS | Check | Result | Date |
| --- | --- | --- | --- | --- | --- |
| Official MCP Python SDK client (`mcp.Client` over stdio subprocess) | 2.2.0 | Ubuntu 24.04, Python 3.11.15 | `tests/test_mcp_server.py`: initialize, list tools, call every tool, pagination caps, missing-node error, corrupt/newer graph startup failure, clean stdout | Pass | 2026-09-23 |
| Claude Code | 2.1.280 | Ubuntu 24.04 | `claude mcp add rootweft -s user -- rootweft mcp GRAPH`, then `claude mcp list` / `claude mcp get rootweft` | Connected. A corrupt graph is reported as "Failed to connect". | 2026-09-23 |
| Gemini CLI | 0.60.0 | Ubuntu 24.04 | `mcpServers` entry in `~/.gemini/settings.json`, trusted folder, `gemini mcp list` | Connected | 2026-09-23 |
| Hermes Agent (`hermes-agent[mcp]`, MCP client SDK 1.26.0) | 0.19.0 | Ubuntu 24.04 | `mcp_servers` entry in `~/.hermes/config.yaml`, `hermes mcp test rootweft` | Connected; 6 tools discovered | 2026-09-23 |
| Codex CLI | 0.156.1 | Ubuntu 24.04 | `[mcp_servers.rootweft]` in `~/.codex/config.toml`, `codex mcp list` | Configuration parsed and enabled. **Connection not exercised** (the command does not start servers); configuration-only. | 2026-09-23 |
| Cursor | — | — | — | Not tested | — |

In the Claude Code, Gemini CLI and Hermes rows the client started
`rootweft mcp` itself and completed the MCP handshake. No row includes a
model-driven tool call inside the harness, because that requires an
authenticated model session; tool behaviour is covered by the SDK row.

## Agent Skill

| Client | Version | OS | Check | Result | Date |
| --- | --- | --- | --- | --- | --- |
| Hermes Agent | 0.19.0 | Ubuntu 24.04 | `rootweft install-skill --target hermes --scope user`, then `hermes skills list` | Listed as `rootweft`, source `local`, `enabled` | 2026-09-23 |
| Claude Code | 2.1.280 | — | `.claude/skills/rootweft/SKILL.md` layout | Installed by `install-skill`; discovery by the client **not verified** | — |
| Codex CLI / Gemini CLI | — | — | `.agents/skills/rootweft/SKILL.md` layout | Installed by `install-skill`; discovery **not verified** | — |
| Cursor | — | — | `.cursor/skills/rootweft/SKILL.md` layout | Not tested | — |

## Reproducing

The checks above can be repeated with `scripts/harness_smoke.sh`, which uses a
throw-away `HOME` so that none of your real client configuration is read or
modified. It needs the client CLIs on `PATH` (for example
`npm install -g @anthropic-ai/claude-code @google/gemini-cli @openai/codex` and
`pip install "hermes-agent[mcp]"`) and `rootweft[mcp]` installed.
