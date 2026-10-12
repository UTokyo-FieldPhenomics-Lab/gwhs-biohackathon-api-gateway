# Global Wheat BioHackathon API Gateway

Single entry point for agents working in this repository. Deeper notes live under `.agents/`.

## Goal

Let BioHackathon participants (Göttingen, 2026-12-07 to 12-11, hybrid) use a global wheat
dataset and segmentation models **without downloading data, deploying models, or owning a
GPU**. Fetching an image and getting a model result are each a single HTTP call.

## Architecture

Two planes, joined only by a frozen contract:

- **Image plane** — S3 (ap-northeast-1, private) behind CloudFront with OAC. Purely static, and
  written once at publish: image objects only. No server, so it never goes down, and no mutable
  object, so there is nothing to invalidate.
- **Metadata plane** — the publisher generates one metadata index per dataset from a pinned
  upstream revision; the gateway loads it from the host at startup and serves it
  from memory. A republish is followed by a restart, which is the whole refresh mechanism.
  This is the one deliberate exception to "static before dynamic".
- **Compute plane** — the same gateway on one EC2 host authenticates, enforces quota and a
  concurrency gate, and routes inference work to GPU compute backends. Its region is environment
  configuration rather than architecture: the dev host runs in `eu-central-1`. A backend is any
  Docker-capable GPU resource reachable over an authenticated, non-public link — a lab
  machine, a GPU VPS, or a rented GPU container; the provider is a deployment choice, not a
  fixed route.
- **Contract plane** — this repository: the OpenAPI surface, the data layout, examples,
  and skills.
- **Domains** — `data.*` serves static images through CloudFront (no auth, long cache);
  `api.*` hits the gateway directly and always requires a key, images included, because
  clients display images from `data.*` rather than through the API. They are deliberately
  separate distributions, never one.

The gateway never carries bytes. It answers with the public `data.*` URL of an object and
resolves every address by lookup in its in-memory index, so no client input is ever concatenated
into a key or a path.

The dataset ids are `gwhd`, `gwfss` and — once the full annotation is released — `gwhs`, plus
`gwhs-temp` for development. Addresses are `(dataset, row)`, where the row is the absolute row
number of the pinned upstream revision; see [`docs/data-layout.md`](docs/data-layout.md).

## Directory Structure

```
.
├── AGENTS.md              agent entry point (this file)
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

## Tech Stack

- **Python** 3.12+, environments and dependencies managed by `uv` (one independent
  `pyproject.toml` + `uv.lock` per Python subproject; no root package).
- **Gateway**: FastAPI + uvicorn + Pydantic v2; an installed package (`src/biohack_gateway`),
  deployed with systemd, not Docker.
- **Datasets**: pyarrow + Pillow + boto3, exposed as the `biohack-datasets` CLI.
- **Docs**: Zensical (pinned) + markdown-exec, published to GitHub Pages.
- **Quality gates**: `pytest` for tests, `mypy` (strict) for types, `ruff` for lint and
  format. Run each with `uv run` inside the subproject directory.

## Coding Style

- Every module, function, and class carries a **NumPy-style docstring** (Sphinx/Napoleon
  compatible). Include `Parameters`, `Returns`, `Raises`, and `Examples` only when they
  add information.
- Comment the *why*, not the *what*: record constraints, invariants, and non-obvious
  decisions. Do not narrate code that already reads plainly.
- Keep the main path visible with guard clauses; treat deep nesting as a design signal.
- Design each function around one coherent responsibility and reason to change.

## Working Notes

`.agents/` holds durable project knowledge, split strictly by kind:

- **`rules/` — constraints, and nothing else.** `project.md` holds repo-wide invariants,
  `design-philosophy.md` holds the process for deciding what to build, and one short file per
  package (`gateway.md`, `datasets.md`, `docs.md`, `deploy.md`) holds package-specific
  constraints. A rule states what must hold and why it was identified; it never contains
  history, log output, anecdotes, or "we tried X". If it cannot be stated in a few lines, it
  is a note, not a rule, and belongs in `references/`.
- **`references/` — detail, decisions, and lessons.** Every non-trivial session writes one
  file named `YYYYMMDDHH_<topic>.md` recording what was done, the commands run, the observed
  results, what broke, and what to do differently next time. This is the only place for
  narrative detail and failure records. Do not restate rules here; link to them.

## Where to Look

- Binding constraints for this repo: [`.agents/rules/project.md`](.agents/rules/project.md)
- Per-package constraints: [`.agents/rules/`](.agents/rules)
- Execution records and lessons: [`.agents/references/`](.agents/references)
- Data layout contract: [`docs/data-layout.md`](docs/data-layout.md)
- Architecture discussion and decision log (SiYuan note, external): "黑客松API服务架构设计"
