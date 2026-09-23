# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x (alpha) | Security fixes on the latest 0.1 release only |
| older | No |

Rootweft is alpha software. There is no long-term support window yet; fixes
ship in a new release rather than as backports.

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Use GitHub's private vulnerability reporting:
<https://github.com/edodelo/rootweft/security/advisories/new>

Include the affected version, platform, a minimal reproduction (a small
synthetic repository or graph file is ideal), and the impact you observed.
Please do not send real secrets or proprietary source code.

What to expect:

- acknowledgement within 7 days;
- an initial assessment within 14 days;
- coordinated disclosure after a fix is released, normally within 90 days of
  the report, with credit if you want it.

This is a volunteer project; these are targets, not contractual guarantees.

## In scope

- Path traversal, symlink/junction escape or reading files the scanner should
  exclude.
- Any network access without `--provider` and `--enable-remote`, or data sent
  beyond what `--dry-run-egress` reports.
- Secrets, source text or credentials reaching a provider, a graph file, logs
  or error messages.
- Script execution or network requests from an exported HTML viewer.
- MCP server capabilities beyond reading the one loaded graph, or protocol
  corruption on stdout.
- Skill installer writes outside its documented destinations, or edits to
  harness configuration or instruction files.
- Overwriting or truncating an existing good graph on failure.

## Out of scope

- Findings that require a malicious local user who already controls your
  shell, Python environment or the Rootweft installation.
- The behaviour of third-party MCP clients, agent harnesses or model providers.
- Denial of service by deliberately raising the documented resource limits.

See [THREAT_MODEL.md](THREAT_MODEL.md) for the design assumptions.
