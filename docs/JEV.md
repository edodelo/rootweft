# Optional Jev adjudication

Rootweft can ask TypeSafe AI's hosted **Jev** decision model to review the
bounded candidates that local parsing could not resolve (for example a
Markdown mention of a name defined in two files). This is optional, off by
default, and never changes the structural layer.

> Jev is a proprietary third-party service. Rootweft is not affiliated with
> TypeSafe AI or OpenRouter. You use your own account and key and must accept
> the provider's current terms and data-handling policies yourself. The
> adapters in v0.1.0-alpha are **contract-tested against the providers'
> published API documentation but not live-verified**.

## What Jev is asked

Rootweft builds all questions locally. Jev may only:

- **Noul** — say whether one candidate relation is supported by the supplied
  labels and evidence coordinates;
- **Choice** — pick one target from a finite list of local node IDs, or `none`;
- **Score** — classify the relevance of a document-to-symbol mention on fixed
  levels.

A response cannot introduce nodes, relation types, paths or commands. Answers
that are malformed, non-finite, out of range, mismatched to the questions or
from an unexpected model are rejected before they reach the graph.

## Providers

| `--provider` | Endpoint (fixed) | Key variable | Model | Reproducible |
| --- | --- | --- | --- | --- |
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` | `jev-1.13.0` (immutable) | Yes, model-wise |
| `openrouter-experimental` | `https://openrouter.ai/api/alpha/decisions` | `OPENROUTER_API_KEY` | route `typesafe/jev-1.13`, TypeSafe-only routing, no fallbacks | No |

There is never an automatic fallback between providers. Moving aliases such as
`jev-latest` are not accepted.

## Running it

```bash
# 1. See exactly what would leave your machine. No request is made.
rootweft build . --provider typesafe --enable-remote --dry-run-egress
# {"candidate_count":1,"destination":"https://api.typesafe.ai/v1/systemone","estimated_bytes":1514,"paths":["README.md","app.py","lib.py"],"provider":"typesafe","question_count":1}

# 2. Run it with your key in the environment (never in a file Rootweft reads).
export TYPESAFE_API_KEY=...
rootweft build . --provider typesafe --enable-remote --output graph.json

# 3. Optional: fail (exit 5) if any required adjudication is incomplete.
rootweft build . --provider typesafe --enable-remote --require-remote --output graph.json

# Connectivity check only (sends a fixed question, no repository content).
rootweft provider-check --provider typesafe --enable-remote
```

`--provider` and `--enable-remote` must both be given on the command line.
They are never read from `.rootweft.toml` or environment variables.

## Budgets

| Flag | Default | Meaning |
| --- | --- | --- |
| `--max-questions` | 32 | Questions per build (one batch) |
| `--max-payload-bytes` | 64000 | Request body size |
| `--max-response-bytes` | 256000 | Response body size |
| `--max-cost-usd` | 0.10 | Conservative input-cost estimate including retries; not a billing guarantee |
| `--max-retries` | 2 | Only for timeouts, DNS errors, 429 and selected 5xx |
| `--timeout-seconds` | 15 | Per-operation timeout |

If a budget would be exceeded, no request is sent.

## Results

Decisions are stored in the `adjudication` layer with status `review` (policy
`rootweft.decision.review.v1`). They are proposals for a human, not accepted
edges. Provenance records provider, requested and returned model, whether the
result is reproducible, question kind, question/state hash, local option IDs,
returned probabilities/values and confidence, policy and UTC timestamp. API
keys and the full request state are never persisted.

Without `--require-remote`, a provider failure still writes the complete
structural graph and records a redacted diagnostic (exit `0`). With it, the
same valid structural graph is written and the command exits `5`.

## Known limitations

- Only labels and evidence coordinates are sent; Jev does not see source code.
  This protects your code but limits how much context the model has.
- There is no automatic promotion policy and no decision cache in this alpha.
  Every run asks again, and every result needs human review.
- Secret detection before egress is heuristic.
- The OpenRouter route may resolve to different dated snapshots over time.
