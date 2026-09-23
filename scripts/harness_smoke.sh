#!/usr/bin/env bash
# Re-run the harness smoke checks recorded in docs/HARNESS_COMPATIBILITY.md.
# Uses a throw-away HOME; never touches your real client configuration.
# Clients that are not installed are reported as "skipped".
set -u

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export HOME="$work/home"
mkdir -p "$HOME"

command -v rootweft >/dev/null || { echo "rootweft not on PATH" >&2; exit 2; }
rootweft --version
rootweft build "$repo_root/examples/sample_repo" --output "$work/graph.json" >/dev/null
graph="$work/graph.json"
cd "$work"

if command -v claude >/dev/null; then
  echo "== Claude Code $(claude --version)"
  claude mcp add rootweft -s user -- rootweft mcp "$graph" >/dev/null
  claude mcp list 2>&1 | grep rootweft
else
  echo "== Claude Code: skipped"
fi

if command -v gemini >/dev/null; then
  echo "== Gemini CLI $(gemini --version)"
  mkdir -p "$HOME/.gemini"
  rootweft mcp-config gemini "$graph" > "$HOME/.gemini/settings.json"
  printf '{"%s":"TRUST_FOLDER"}\n' "$work" > "$HOME/.gemini/trustedFolders.json"
  gemini mcp list 2>&1 | grep rootweft
else
  echo "== Gemini CLI: skipped"
fi

if command -v hermes >/dev/null; then
  echo "== Hermes Agent"
  mkdir -p "$HOME/.hermes"
  rootweft mcp-config hermes "$graph" > "$HOME/.hermes/config.yaml"
  hermes mcp test rootweft 2>&1 | grep -E "Connected|Tools discovered|failed"
  rootweft install-skill --target hermes --scope user >/dev/null
  hermes skills list 2>&1 | grep rootweft
else
  echo "== Hermes Agent: skipped"
fi

if command -v codex >/dev/null; then
  echo "== Codex CLI $(codex --version 2>/dev/null)"
  mkdir -p "$HOME/.codex"
  rootweft mcp-config codex "$graph" > "$HOME/.codex/config.toml"
  codex mcp list 2>/dev/null | grep rootweft
else
  echo "== Codex CLI: skipped"
fi
