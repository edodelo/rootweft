# Contributing to Rootweft

Thanks for your interest. Rootweft is a small alpha project; issues, bug
reports with minimal reproductions and focused pull requests are welcome.

## Ground rules

- Keep Rootweft an independent implementation. Do not paste code, tests,
  prompts, documentation or assets from other code-graph tools (including
  Graphify) or from sources whose license is incompatible with Apache-2.0.
- Security issues go through private reporting, not public issues. See
  [SECURITY.md](SECURITY.md).
- Offline by default is a hard requirement. New network access must be behind
  the existing explicit `--provider` + `--enable-remote` gate.
- The structural graph must stay deterministic: same input, same bytes.
- Write the failing test first, then the change. Tests should fail for a real
  behavioural reason, not because a string in a document changed.
- By submitting a contribution you agree it is licensed under Apache-2.0
  (inbound = outbound). Please sign off commits (`git commit -s`) to certify
  the [Developer Certificate of Origin](https://developercertificate.org/).

## Development setup

```bash
git clone https://github.com/edodelo/rootweft
cd rootweft
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[mcp]" -r requirements-dev.txt
```

`uv.lock` records a fully resolved dependency set for reproducible
environments (`uv sync --extra mcp`); regenerate it with `uv lock` when
dependencies change.

## Checks (the same gates CI runs)

```bash
ruff check .
ruff format --check .
mypy src/rootweft
python -m pytest --cov=rootweft --cov-fail-under=80
python -m build
python scripts/verify_release.py --dist dist --allow-dirty   # packaging sanity
```

Some scanner tests are platform-specific (Windows junctions, POSIX FIFOs) and
skip elsewhere. The optional browser test of the HTML viewer runs only when
`ROOTWEFT_BROWSER_EXECUTABLE` and Playwright for Node are available.

## Pull requests

- One logical change per pull request, with tests.
- Update `CHANGELOG.md` under "Unreleased" for user-visible changes.
- Update documentation when you change a command, flag, tool or file format.
  Examples in the docs should be commands you actually ran.
- Do not commit graph files built from private repositories.
