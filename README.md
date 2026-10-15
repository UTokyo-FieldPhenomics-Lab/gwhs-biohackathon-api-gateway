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
| Image | Serves images (and masks) | S3 (private, `eu-central-1`) behind CloudFront with OAC — written once at publish, no server, never down, and nothing mutable: a change is a new path, not an invalidation |
| Metadata | Serves metadata, annotations, facets, and addressing | The publisher generates one index per dataset from a pinned upstream revision; the gateway loads it at startup and answers from memory |
| Compute | Authenticates today, and serves the dataset surface; routes inference work to GPU backends once the model surface is written | One FastAPI gateway on an EC2 host (region per environment) routing to Docker-capable GPU backends over an authenticated, non-public link — a lab machine, a GPU VPS, or a rented GPU container |
| Contract | Defines the API surface and the data layout | This repository: OpenAPI models, `docs/data-layout.md`, examples, skills |

Metadata is the one deliberate exception to static-first: it lives in the gateway's memory so a
republish takes effect immediately and no client has to hold a copy.

The bucket and the host that publishes into it share a region, so the publisher reads a row's bytes
out of its cached parquet and PUTs them in one request: nothing is staged on disk, and a publish
moves no bytes between regions. How the bucket, the distribution, the certificate, and the domain
are built is in [`deploy/README.md`](deploy/README.md).

Three domains, deliberately separate:

```
data.global-wheat.com  → CloudFront → S3         static images, no auth, long cache
api.global-wheat.com   → gateway    → backends   dynamic requests, always requires a key
docs.global-wheat.com  → GitHub Pages            the documentation site, static, disposable
```

The first two are ours to run; the third is GitHub's, which is why the documentation stays readable
while the host is stopped. `api.global-wheat.com/docs` remains the interactive reference, and the site
frames that page rather than copying it.

The gateway **never carries bytes**: it answers with the public `data.*` URL of an object, which
is what clients display, so bandwidth stays on the CDN and off the gateway. Every address is
resolved by lookup in the in-memory index, never assembled from client input.

Addresses are `(dataset, row)` — the absolute row number of the pinned upstream revision, which is
meaningless without its dataset. Dataset ids are `gwhd`, `gwfss`, and — once the full annotation
is released — `gwhs`; `gwhs-temp` is a development-only sample.

## Repository layout

The tree, file by file, is in [`AGENTS.md`](AGENTS.md#directory-structure). Three Python subprojects
— `gateway/`, `datasets/`, `docs/` — each with its own `pyproject.toml` and `uv.lock` and no root
package, plus `deploy/` for the host. Deeper notes and constraints live under [`.agents/`](.agents/).

## Getting started

Work through [`deploy/README.md`](deploy/README.md) once, when the host, its API surface, or the
dataset bucket does not exist yet: that is where the machine, the checkout, and the data plane are
created, and the checkout lands at a fixed path. Every session afterwards starts in it — if you are not
sure where the repository is, that path is the default:

```bash
export CHECKOUT=/opt/gwhs-biohackathon-api-gateway   # the checkout: prepare-host.sh clones it here
cd "$CHECKOUT"
```

Then work inside the subproject you need rather than at the root — `cd gateway`, `cd datasets`, and so
on. Each is independent, with its own environment, and
[uv](https://docs.astral.sh/uv/) fetches Python 3.12+ automatically if it is missing.

### Gateway

```bash
cd gateway
uv sync                                          # create .venv, install deps + this package
uv run pytest                                    # tests
uv run mypy .                                    # strict type check
uv run ruff check . && uv run ruff format .      # lint and format
```

Serve it. This one stays in the foreground until you stop it, so it wants its own terminal:

```bash
uv run uvicorn biohack_gateway.app:app --reload
```

`/health` and the documentation pages answer without a key; everything else needs a participant token,
so issue one. The pool lives at the same path on every machine, host and laptop alike, which is what
keeps the two behaving the same. The three verbs:

```bash
uv run biohack-gateway tokens add --id dev       # issue; the token goes to stdout, once
uv run biohack-gateway tokens list               # team ids only, never the tokens, so it is safe to paste
uv run biohack-gateway tokens remove --id dev    # revoke
```

Now call it:

```bash
curl -s localhost:8000/health                    # {"status":"ok","version":"0.1.0"}
curl -s localhost:8000/                          # 401 invalid_key
curl -s -H "X-API-Key: <token>" localhost:8000/  # 404 not_found, which means the key was accepted
```

The gateway re-reads the pool whenever the file changes, so a token issued, rotated or revoked applies
on the next request — there is nothing to restart. `--force` rotates an existing team's token without
changing its id — what a leaked credential needs — and `--file` points any of the three verbs at a copy
instead of the default pool: a laptop, or the copy kept off the host. The exact flags are in
`biohack-gateway tokens --help`; issuing tokens on the host during the event, and keeping that copy
fresh: [`deploy/README.md`](deploy/README.md).

The dataset surface is part of that same process and needs a key like everything else. It loads
`~/.config/biohack-gateway/index/<dataset>.db` at startup and answers from memory, so a republish
takes effect by restarting the service rather than by an invalidation:

```bash
curl -s -H "X-API-Key: <token>" localhost:8000/datasets                      # the directory
curl -s -H "X-API-Key: <token>" localhost:8000/datasets/gwhs-temp            # facets and palette
curl -s -H "X-API-Key: <token>" 'localhost:8000/datasets/gwhs-temp/images?limit=2'
curl -s -H "X-API-Key: <token>" localhost:8000/datasets/gwhs-temp/images/0   # one row's geometry
```

The gateway reads its index from the path above and addresses images at
`https://data.global-wheat.com`; both are `create_app` defaults, and it reads no host environment.
What the responses mean — the response fields themselves — is the [interactive API reference](https://api.global-wheat.com/docs); the internal contract behind them is `docs/data-layout.md`, which is not published on the site.

### Datasets

The publisher produces both artifacts of a release in one run: image objects in S3, and one
SQLite metadata index per dataset, which is the file the gateway loads at startup. It is one
command with two subcommands — `fetch` fills the local upstream cache, `publish` runs
build → validate → upload → verify — and a non-zero exit code is the only signal that stops the
pipeline.

Publishing writes into the dataset bucket and **the gateway never does** — it does not know the bucket
name and has no S3 client — so the publisher carries its own credential, scoped to the dataset prefixes:
an IAM user whose access-key CSV and environment live together in
`~/.config/biohack-gateway/publish/`, a directory the gateway's service is configured not to be able to
read. Preparing it once, in the console, is [`deploy/README.md`](deploy/README.md) §5; the variable
names alone are recorded in [`datasets/.env.example`](datasets/.env.example).

Upstream files are not committed and not shipped: they are fetched into a local, disposable cache
by a separate step, which reads the pinned revisions in `sources.yaml` and downloads only the
files each dataset selects — never the archives and cards sitting at the upstream root. Run it on
the host that will publish, which is the host that holds the cache:

```bash
cd datasets
uv sync
uv run biohack-datasets fetch --dry-run            # print the plan: files, sizes, what is cached
uv run biohack-datasets fetch                      # fetch everything into datasets/.hf_cache/
uv run biohack-datasets fetch --dataset gwhs-temp  # one dataset
```

The cache is 1.88 GB for all three datasets, holds upstream bytes verbatim under
`datasets/.hf_cache/<dataset_id>/`, and is rebuilt from the pinned revision rather than preserved.
`HF_TOKEN` raises the Hub rate limits and is used when it is set; nothing needs it today.

A mask dataset also pins the palette its annotation was painted with, and `fetch` downloads that one
file too, into `datasets/.hf_cache/<dataset_id>/labels/`. A publish without it stops rather than
building an index whose classes are silently empty.

```bash
uv run biohack-datasets publish --dataset gwhs-temp --dry-run                # the plan, including the bucket
uv run biohack-datasets publish --dataset gwhs-temp --index-dir ./build     # build locally, no upload
uv run biohack-datasets publish --dataset gwhs-temp --bucket s3://<bucket>  # build → upload → verify
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
uv run pytest -m network                         # the Hub is reachable: pinned plan, real download
```

A publish never downloads: it reads the cache `fetch` filled, and when that cache is missing it
exits non-zero naming `biohack-datasets fetch`. A cache holding only part of a dataset is refused
the same way, because a missing shard would give every later row an address that is not its own.
The index is host state rather than checkout
content — `~/.config/biohack-gateway/index/<dataset>.db`, override with `--index-dir` — and
`gwhs-temp` is the rehearsal target the whole path is proven on. The publishing credential is
[`deploy/README.md`](deploy/README.md) §5.

### Documentation site

The curated documentation is live at <https://docs.global-wheat.com>, built by Zensical from
`docs/content/` and published to GitHub Pages by [`.github/workflows/docs.yml`](.github/workflows/docs.yml)
on every push to `main`. Four pages, all written for a participant: `index.md` (the calls and the
dataset list), `datasets/` (what each dataset contains, with images, and how a response maps onto
them), `post-processing/` (palette masks and RLE geometry, in Python, JavaScript, and Kotlin, with the
snippets as downloadable files), and `interactive.md` (FastAPI's own Swagger UI, embedded in a frame,
with a direct link for a network that cannot reach `api.*`). The internal contract, `data-layout.md`,
is deliberately not published.

```bash
uv sync --locked --project docs                    # from the repository root
uv run --project docs zensical build --strict      # a warning fails the build
uv run --project docs zensical serve -a 127.0.0.1:8765
```

`site_dir` must stay outside `docs/content/`, and so must the virtualenv: a `.venv` anywhere under
the content root silently stops the build putting `site/assets/` in the output, which serves an
unstyled site. The hand steps behind the domain are [`deploy/README.md`](deploy/README.md) §6.

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
