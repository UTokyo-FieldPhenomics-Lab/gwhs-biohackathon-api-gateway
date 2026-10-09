# Global Wheat BioHackathon API Gateway

Dataset and model API for the global wheat BioHackathon (Göttingen, 2026-12-07 to 12-11).

## What this is

Participants should be able to build an agricultural application **without downloading the
dataset, deploying a model, or owning a GPU**. Two things carry the whole experience, and
each is a single HTTP call:

- fetch one image,
- get one model result.

Everything else — documentation, examples, a task pool, cost accounting — is supporting
material. The project is deliberately small and static-first: it delays any component until
a concrete need for it appears, and records the trigger rather than building ahead of it.

## Architecture

Two planes joined only by a frozen contract:

| Plane | What it does | How |
|---|---|---|
| Data | Serves images, metadata, annotations, and index files | S3 (private) behind CloudFront with OAC — purely static, no server, never down |
| Compute | Authenticates, enforces quota and a concurrency gate, routes work | One FastAPI gateway on EC2 (Tokyo) routing to Docker-capable GPU backends over an authenticated, non-public link — a lab machine, a GPU VPS, or a rented GPU container |
| Contract | Defines the API surface and the data layout | This repository: OpenAPI models, `docs/data-layout.md`, examples, skills |

Two domains, deliberately separate:

```
data.<domain>  → CloudFront → S3        static assets, no auth, long cache, fallback path
api.<domain>   → gateway   → backends   dynamic requests, always requires a key
```

The gateway **never proxies image bytes**: it answers `302` redirects to the static plane, so
bandwidth stays on the CDN and off the gateway.

## Repository layout

```
.
├── AGENTS.md              agent entry point: goal, architecture, stack, style
├── .agents/
│   ├── rules/             short, binding constraints (project.md + design-philosophy.md + one per package)
│   ├── references/        detailed execution records and lessons learned
│   └── skills/            packaged agent skills (planned: API + data layout)
├── docs/                  documentation site sources (Zensical)
│   ├── data-layout.md     handwritten contract: S3 layout, index fields, manifest fields
│   ├── api/               API reference markdown, generated and committed
│   ├── notebooks/         tutorial sources (markdown is the source, .ipynb is derived)
│   └── examples/          curl / python / js snippets, executed in CI
├── gateway/               FastAPI gateway: auth, quota, gate, routing
│   ├── src/biohack_gateway/  installed app package (app.py, models, routing)
│   └── scripts/           contract projections, e.g. gen_api_docs.py
├── datasets/              publisher CLI: build + sync the CDN-ready static layout
├── deploy/                bootstrap script, systemd units, reverse-proxy config
└── .github/workflows/     planned: secret scan, contract guard, tests, lock, docs
```

Each Python subproject is independent: its own `pyproject.toml` and `uv.lock`, no root
package. Deeper notes and constraints live under [`.agents/`](.agents/) and in
[`AGENTS.md`](AGENTS.md).

## Getting started

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12+ is fetched automatically if missing).
Start inside the subproject you are working on — there is no root environment.

### Gateway

```bash
cd gateway
uv sync                                          # create .venv, install deps + this package
uv run uvicorn biohack_gateway.app:app --reload  # run the API locally (skeleton: no endpoints yet)
uv run pytest                                    # tests
uv run mypy .                                    # strict type check
uv run ruff check . && uv run ruff format .      # lint and format
```

### Datasets

The publisher builds the CDN-ready layout and, with `--bucket`, uploads and re-verifies it.
A non-zero exit code is the only signal that forbids tearing down the temporary instance.

```bash
cd datasets
uv sync
uv run biohack-datasets --release v1.0 --dry-run                 # print the plan, change nothing
uv run biohack-datasets --release v1.0                           # build locally, no upload
uv run biohack-datasets --release v1.0 --bucket s3://<bucket>    # build → upload → verify
uv run pytest && uv run mypy && uv run ruff check .
```

The publisher is not implemented yet: a valid invocation parses its arguments and then raises
`NotImplementedError`. Nothing is built or uploaded. The interface is fixed; the
implementation lands next.

### Documentation site

```bash
cd docs
uv sync
uv run zensical serve    # preview the site locally
uv run zensical build    # build the static site
```

The docs site configuration is not committed yet; add the Zensical config before building.

## Changing dependencies

Edit the subproject's `pyproject.toml`, then re-lock so CI stays reproducible:

```bash
cd gateway   # or datasets
uv lock
```

## Conventions

- Project language is English: code, comments, docstrings, and docs.
- Every module, function, and class carries a NumPy-style docstring.
- Formatting, linting, and typing are enforced by `ruff` and `mypy --strict`; see
  [`AGENTS.md`](AGENTS.md) and [`.agents/rules/project.md`](.agents/rules/project.md).

## License

MIT — see [LICENSE](LICENSE).
