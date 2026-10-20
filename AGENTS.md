# Global Wheat BioHackathon API Gateway

Agent entry point for this repository: the constraints, the layout, and where to look. What the
project is and why it is built this way is [`README.md`](README.md); durable notes are under
`.agents/`.

## Goal

Let BioHackathon participants (Göttingen, 2026-12-07 to 12-11, hybrid) use a global wheat dataset
and segmentation models **without downloading data, deploying models, or owning a GPU**: fetching an
image and getting a model result are each a single HTTP call.

## Architecture

Two planes joined only by a frozen contract, plus the compute the model surface routes to:

- **Image plane** (live) — private S3 behind CloudFront. Image objects only, written once at publish
  by a PUT from the publishing host in the bucket's own region: no server to go down, and no mutable
  object, so a change is a new path rather than an invalidation.
- **Metadata plane** (live) — the publisher generates one index per dataset from a pinned upstream
  revision; the gateway loads it at startup and serves it from memory, so a republish arrives as a
  restart. This is the one deliberate exception to static-first.
- **Compute plane** (surface implemented and rehearsed against a real backend; **not deployed**) — the
  same gateway on one EC2 host authenticates and routes inference work to GPU backends. Its region is
  environment configuration: the host runs in `eu-central-1`. A backend is any Docker-capable GPU resource reachable over an
  authenticated, non-public link — a lab machine, a GPU VPS, or a rented GPU container; the provider
  is a deployment choice, not a fixed route. The backends' containers live in a **private**
  repository that this one links as the submodule at `models/`, so the containers and the weights
  they load stay private while the contract stays public;
  [`rules/models.md`](.agents/rules/models.md) is what that link must and must not do.
- **Contract plane** — this repository: the OpenAPI surface, the data layout, examples, and the
  participant-facing site at <https://docs.global-wheat.com>.

The gateway never carries bytes: it answers with the public `data.*` URL of an object and resolves
every address by lookup in its in-memory index, so no client input is ever concatenated into a key or
a path. The three domains, and why they are separate distributions, are in [`README.md`](README.md).

The dataset ids are `gwhd`, `gwfss` and — once the full annotation is released — `gwhs`, plus
`gwhs-temp` for development. Addresses are `(dataset, row)`, where the row is the absolute row number
of the pinned upstream revision; see [`docs/data-layout.md`](docs/data-layout.md).

## Directory Structure

```
.
├── AGENTS.md              agent entry point (this file)
├── .agents/
│   ├── rules/             short, binding constraints (project.md + design-philosophy.md + one per package)
│   ├── references/        detailed execution records and lessons learned
│   └── plans/             agreed execution plans; a finished one is renamed with ✅ and left as an archive
├── zensical.toml          docs site config: docs/content → site/, published to GitHub Pages
├── docs/                  documentation subproject (Zensical)
│   ├── data-layout.md     internal contract: object layout, addressing, index fields — unpublished
│   ├── scripts/           make_figures.py (every figure), make_notebook.py (the .ipynb),
│   │                      record_notebook.py (the quickstart's recorded outputs),
│   │                      make_models.py (a model page's parameter table), sync_clients.py
│   └── content/           the site's pages and assets: the only thing Zensical builds
│       ├── api/           the contract, the clients, the framed reference, and the frame's script
│       ├── datasets/      what a row is, then one page per dataset, with their figures
│       ├── models/        the GPUs behind the API, then one page per model
│       └── examples/      the live demo, and the notebook page the .ipynb is generated from
├── clients/               the API as a function, one directory per language; routes.json is shared
│   ├── python/            global_wheat.py + its pyproject, lock, and the pytest checks
│   ├── js/                global-wheat.js + package.json and node --test checks
│   └── java/              GlobalWheat.java + tests/Checks.java (a local HTTP server, no dependency)
├── gateway/               FastAPI gateway: the request gate and the dataset surface
│   └── src/biohack_gateway/  installed app package (app, auth, tokens, dataset surface, CLI)
├── datasets/              upstream parquet cache + publisher CLI: parquet → image objects in S3 + metadata index
├── models/                submodule: the private container repository the GPU backends run
├── deploy/                host bring-up, the dataset bucket and its CDN, bootstrap scripts
└── .github/workflows/     docs.yml builds and publishes the site
```

## Tech Stack

- **Python** 3.12+, environments and dependencies managed by `uv` (one independent
  `pyproject.toml` + `uv.lock` per Python subproject; no root package).
- **Gateway**: FastAPI + uvicorn + Pydantic v2; an installed package (`src/biohack_gateway`),
  deployed with systemd, not Docker.
- **Datasets**: pyarrow + Pillow + boto3, exposed as the `biohack-datasets` CLI.
- **Docs**: Zensical (pinned) + markdown-exec, built from `docs/content/` and published to
  <https://docs.global-wheat.com> by `.github/workflows/docs.yml`.
- **Clients**: one dependency-light file per language — Python (`numpy` + `requests`),
  JavaScript (none), Java (the JDK) — downloaded from the site, never published to an index.
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

## Commits

- **Check for a token or a private network address before every commit, not before every publish.**
  A commit is the irreversible step; a publish can be changed, and a history cannot be taken back
  from a clone. Test the *shape*, not a list of addresses we happen to know: a list of ours catches
  only yesterday's leak — the gateway host's own Elastic IP was missing from the first version of
  this rule. Both of these must come back empty:

  ```sh
  # 1. any IPv4 that is not a documentation address, a loopback, the IMDS endpoint, the
  #    tailnet's own 100.64.0.0/10 base, or Windows' OpenSSH.Server~~~~0.0.1.0 feature name
  git diff --cached -U0 | grep -E '^\+' \
    | grep -oE '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' \
    | grep -vE '^(127\.0\.0\.1|0\.0\.0\.0|0\.0\.1\.0|169\.254\.169\.254|100\.64\.0\.0|192\.0\.2|198\.51\.100|203\.0\.113)'

  # 2. a literal handed to a credential-shaped name (tests hold constants, so they are skipped)
  git diff --cached -U0 | grep -E '^\+' | grep -v '/tests/' \
    | grep -iE "(key|token|secret|password)[[:space:]]*[=:][[:space:]]*[\"'][A-Za-z0-9_/+-]{16,}"
  ```

  A real address belongs in the untracked `.agents/local/`, per
  [`rules/project.md`](.agents/rules/project.md) §Security; the ranges to *write* in a document are
  `192.0.2.0/24`, `198.51.100.0/24` and `203.0.113.0/24`. Rotating a token is a minute, and
  rewriting a public history is not.

## Working Notes

`.agents/` holds durable project knowledge, split strictly by kind:

- **`rules/` — constraints, and nothing else.** `project.md` holds repo-wide invariants,
  `design-philosophy.md` holds the process for deciding what to build, and one short file per
  package (`gateway.md`, `datasets.md`, `docs.md`, `deploy.md`, `models.md`) holds package-specific
  constraints. A rule states what must hold and why it was identified; it never contains
  history, log output, anecdotes, or "we tried X". If it cannot be stated in a few lines, it
  is a note, not a rule, and belongs in `references/`. `models.md` is the one file describing a
  directory that is not part of this repository's own tree: `models/` is a submodule, and the rules
  for operating it are still this repository's to keep. `backend-pool.md` is the one file that is not
  a package's: it holds the machines that serve a model, who may reach them, and one router per fault
  domain, and it is kept current in place because an agent acting on a stale row acts on a machine
  that has moved.
- **`references/` — detail, decisions, and lessons.** Every non-trivial session writes one
  file named `YYYYMMDDHH_<topic>.md` recording what was done, the commands run, the observed
  results, what broke, and what to do differently next time. This is the only place for
  narrative detail and failure records. Do not restate rules here; link to them.
- **`plans/` — agreed execution plans.** `YYYYMMDDHH_<topic>.md`, kept current while the work is
  open, and renamed `YYYYMMDDHH_✅<topic>.md` when it is finished so it stops being updated. It is
  an archive at that point; the record of what actually happened belongs in `references/`.

## Where to Look

- Binding constraints for this repo: [`.agents/rules/project.md`](.agents/rules/project.md)
- Per-package constraints: [`.agents/rules/`](.agents/rules) — `clients.md` is what the three client
  files must and must not be (no response models, identical names, decoders only here)
- The containers behind the GPU backends, and the link to their private repository:
  [`.agents/rules/models.md`](.agents/rules/models.md) — what `models/` is for, and the constraints
  that keep it inert for this repository's own build and tests.
- The machines that serve a model, and who may reach them:
  [`.agents/rules/backend-pool.md`](.agents/rules/backend-pool.md) — the pool itself, kept current,
  and the reason an address there means nothing outside the lab's tailnet. A machine is named there
  by the alias its answers carry, and the alias-to-address map is `.agents/local/pool.md`: untracked,
  and a clone without it resolves no machine.
- Execution records and lessons: [`.agents/references/`](.agents/references)
- A host that answers from elsewhere but not from one network:
  [`references/2026101513_host-unreachable-from-the-utokyo-network.md`](.agents/references/2026101513_host-unreachable-from-the-utokyo-network.md)
  — it was never the network: an accepted tailnet route for the client /24 sent the host's replies
  into the tunnel. Capture on the host and read `ip route get <client-ip>` before blaming a path.
- Data layout contract: [`docs/data-layout.md`](docs/data-layout.md)
- Architecture discussion and decision log (SiYuan note, external): "黑客松API服务架构设计"
