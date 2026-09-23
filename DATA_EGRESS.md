# Data egress

Rootweft sends data off your machine in exactly one situation: a `build` or
`provider-check` command with **both** `--provider NAME` and
`--enable-remote` typed on the command line. Nothing else — configuration
files, environment variables, the MCP server, the viewer or the skill — can
cause a network request.

## Destinations

| Provider | HTTPS endpoint | Credential |
| --- | --- | --- |
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` |
| `openrouter-experimental` | `https://openrouter.ai/api/alpha/decisions` (OpenRouter forwards to TypeSafe) | `OPENROUTER_API_KEY` |

The origin is fixed in code. TLS verification is always on, redirects are
disabled, environment proxy settings are ignored, and there is no fallback to
another provider.

## What is sent

One request body per build containing:

- the requested model identifier;
- a "state" text that lists, for each candidate: its ID and relation, and for
  the source node and each option node the node ID, name, qualified name,
  kind, language and evidence coordinates (relative path, start/end line);
- one question per candidate (Noul, Choice or Score) with fixed instructions
  and the local option IDs.

**Not sent:** source code, file contents, snippets, absolute paths, node
metadata, environment variables, credentials other than the provider key in
the `Authorization` header, or anything about files outside the candidates.

`provider-check` sends only a fixed connectivity question with no repository
data.

## Safeguards before sending

- Candidates whose evidence path or any option path lies in an excluded
  location (VCS data, environment files, credential names, key files, vendor
  and dependency trees, caches) are refused.
- Every label and the whole request body pass a secret detector (heuristic:
  private keys, common token formats and high-risk assignments). A hit blocks
  the request.
- Request size, question count, option count, response size, retries, timeout
  and a conservative cost estimate are bounded (see [docs/JEV.md](docs/JEV.md)).
  If any budget would be exceeded, nothing is sent.

## Preview

```bash
rootweft build PATH --provider typesafe --enable-remote --dry-run-egress
```

prints the provider, destination, candidate and question counts, every
relative path that would appear in the request and the estimated request size.
It constructs no provider object and opens no socket, and it writes no graph.

## Retention and deletion

Rootweft stores only the provider's answers and provenance in the local graph
file; delete the file to delete them. Rootweft cannot delete data held by the
provider. Consult TypeSafe AI's and OpenRouter's current policies before
sending proprietary names or paths; retention terms were not verified for this
release.
