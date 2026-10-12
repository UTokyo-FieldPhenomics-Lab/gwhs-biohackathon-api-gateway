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
| Image | Serves images (and masks) | S3 (private) behind CloudFront with OAC — written once at publish, no server, never down, and nothing mutable to invalidate |
| Metadata | Serves metadata, annotations, facets, and addressing | The publisher generates one index per dataset from a pinned upstream revision; the gateway loads it at startup and answers from memory |
| Compute | Authenticates, enforces quota and a concurrency gate, routes work | One FastAPI gateway on an EC2 host (region per environment) routing to Docker-capable GPU backends over an authenticated, non-public link — a lab machine, a GPU VPS, or a rented GPU container |
| Contract | Defines the API surface and the data layout | This repository: OpenAPI models, `docs/data-layout.md`, examples, skills |

Metadata is the one deliberate exception to static-first: it lives in the gateway's memory so a
republish takes effect immediately and no client has to hold a copy.

Two domains, deliberately separate:

```
data.<domain>  → CloudFront → S3        static images, no auth, long cache
api.<domain>   → gateway   → backends   dynamic requests, always requires a key
```

The gateway **never carries bytes**: it answers with the public `data.*` URL of an object, which
is what clients display, so bandwidth stays on the CDN and off the gateway. Every address is
resolved by lookup in the in-memory index, never assembled from client input.

Addresses are `(dataset, row)` — the absolute row number of the pinned upstream revision, which is
meaningless without its dataset. Dataset ids are `gwhd`, `gwfss`, and — once the full annotation
is released — `gwhs`; `gwhs-temp` is a development-only sample.

## Repository layout

```
.
├── AGENTS.md              agent entry point: goal, architecture, stack, style
├── .agents/
│   ├── rules/             short, binding constraints (project.md + design-philosophy.md + one per package)
│   ├── references/        detailed execution records and lessons learned
│   └── skills/            packaged agent skills (planned: API + data layout)
├── docs/                  documentation site sources (Zensical)
│   ├── data-layout.md     handwritten contract: object layout, addressing, index fields
│   ├── api/               API reference markdown, generated and committed
│   ├── notebooks/         tutorial sources (markdown is the source, .ipynb is derived)
│   └── examples/          curl / python / js snippets, executed in CI
├── gateway/               FastAPI gateway: model surface (auth, quota, gate, routing)
│   ├── src/biohack_gateway/  installed app package (app, models, routing, dataset surface)
│   └── scripts/           contract projections, e.g. gen_api_docs.py
├── datasets/              publisher CLI: parquet → image objects in S3 + metadata index
├── deploy/                bootstrap scripts, systemd units, reverse-proxy config
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

The publisher produces both artifacts of a release in one run: image objects in S3, and one
metadata index per dataset on the host's data volume — the file the gateway loads at startup. A
non-zero exit code is the only signal that forbids tearing down the temporary instance.

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
