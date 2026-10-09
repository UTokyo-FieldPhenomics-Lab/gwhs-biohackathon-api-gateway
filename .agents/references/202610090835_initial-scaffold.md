# Initial Repository Scaffold

Date: 2026-10-09 08:35 JST
Topic: creating the directory skeleton, per-package `pyproject.toml`, `AGENTS.md`,
`.agents/rules/`, and the docs/format conventions for this repository.

## Objective

Turn an empty repository (only `README.md`, `LICENSE`, `.gitignore`) into the structure that
the architecture discussion settled on, with working per-package environments and a written
set of binding constraints.

## What was done

1. Read the SiYuan note `20261008215136-w5dm68m` ("黑客松API服务架构设计") with
   `siyuan-kernel export md --id ... -w /home/crest/SiYuan` to recover the agreed structure
   and the measured facts it rests on.
2. Created the directory tree: `gateway/`, `datapack/`, `docs/`, `deploy/`,
   `.github/workflows/`, `.agents/{rules,skills}`.
3. Initialized one independent `pyproject.toml` per Python subproject and generated lock
   files for `gateway/` and `datapack/`.
4. Wrote `AGENTS.md` (goal, architecture, layout, stack, coding style).
5. Wrote `.agents/rules/project.md` plus one file per package (`gateway.md`, `datapack.md`,
   `docs.md`, `deploy.md`).
6. Updated `README.md` for human readers: intro, architecture, layout, per-package commands.
7. Replaced the empty `README` shipped from the initial commit with the above.

## Decisions taken (with the reason)

| Decision | Reason |
|---|---|
| `datapack/` at top level, not `tools/datapack/` | The note's §7 prose and §8 tree disagreed. Chosen: §8. Two Python subprojects do not justify an extra grouping level. |
| `docs/pyproject.toml` instead of `requirements.txt` | The note leans toward pinned `requirements.txt`; a `pyproject.toml` was preferred for consistency. The "no lock file" stance was kept. |
| `requires-python = ">=3.12"`, lock now | Broad compatibility; 3.14 (local) risks missing wheels for scientific dependencies later. |
| No root `pyproject.toml` | Subprojects are independent; a workspace adds a coupling with no benefit at this size. |
| `[tool.uv] package = false` for gateway and docs | They are applications/build targets, not importable distributions. uv installs dependencies without building the project. |
| datapack keeps a package + console script | It ships a CLI (`gwhs-datapack`) and therefore needs a build backend (hatchling) and a `src/` layout. |
| Keep `project.md` alongside per-package rule files | Security, contract sources, and scope discipline are repo-wide; splitting them per package would duplicate. |
| Rules vs references split | Rules state constraints; narrative detail and failures go to `.agents/references/`. Enforced in `AGENTS.md`. |

## Verification evidence

Commands run, from the respective subproject directories:

| Command | Result |
|---|---|
| `gateway: uv lock` | Resolved 36 packages |
| `gateway: uv run python -c "from app import app; ..."` | `GWHS API 0.1.0` |
| `gateway: uv run ruff check .` / `ruff format --check .` | All checks passed / already formatted |
| `gateway: uv run mypy .` | Success, no issues in 1 source file |
| `gateway: uv run pytest` | No tests ran (expected; test dirs are stubs) |
| `datapack: uv lock` | Resolved 24 packages |
| `datapack: uv run gwhs-datapack --version v1.0 --dry-run` | Entry point resolves; raises `NotImplementedError` by design |
| `datapack: uv run ruff check .` / `ruff format --check .` | All checks passed / 2 files already formatted |
| `datapack: uv run mypy` | Success, no issues in 2 source files |
| `gateway venv: import uvicorn/fastapi/pydantic` | uvicorn 0.54.0, fastapi 0.143.0, pydantic 2.14.0 |
| `docs: uv lock` (in a temp dir, not committed) | zensical 0.0.63 and markdown-exec 1.12.4 resolve |
| `uv run zensical --help` | Subcommands are `build`, `new`, `serve` |

## Lessons learned

- **The architecture note can contradict itself.** §7 said `tools/datapack/`, §8 said
  `datapack/`. Reading the whole note and cross-checking before building avoided committing
  to the wrong tree.
- **Read the note in full before scaffolding.** Half the structural choices (flat gateway vs
  `src/` layout, `sources.yaml`, the docs pin) are only visible in later sections.
- **`uv lock` resolves without installing.** No network-heavy sync was needed to produce the
  lock files; `.venv` appears on the first `uv run`.
- **`.gitignore` ships with `uv.lock`, `.venv`, `.ruff_cache`, and `.mypy_cache` already
  handled.** `.gitignore` (line 102) leaves `uv.lock` un-ignored, so the locks will be
  tracked, which matches the reproducibility stance in the rules.
- **uv picks the highest available interpreter in range** (3.13.11 here), not the local
  default (3.14.7). `requires-python` is a floor, not a pin.
- **`[tool.uv] package = false` is the correct marker for an application.** Without it, uv
  expects a build backend and fails to sync a project that has only loose modules.
- **Zensical's CLI is `build` / `new` / `serve`** (verified, not assumed) — worth confirming
  before documenting commands that live in `README.md`.
- **ruff's numpy docstring convention** is set with `[tool.ruff.lint.pydocstyle]` while
  `D` is enabled in `[tool.ruff.lint].select`; both are needed for it to take effect.
- **Repeated rules are not "reinforcement", they are drift.** The first draft restated
  "never proxy bytes", "`/v1` frozen", secrets, and immutability in both `project.md` and the
  package files. Two copies of a rule will diverge on the next edit. Fix: `project.md` owns
  cross-cutting invariants, package files carry only what is specific and explicitly defer to
  `project.md`.

## Addendum: design philosophy and the sha256 question

Added `.agents/rules/design-philosophy.md` (first-principles + Occam check before building,
with hashing as a worked example). It exposes a direct conflict with the rules written
earlier the same day:

| Location | Current text | Conflict |
|---|---|---|
| `.agents/rules/datapack.md` | "Idempotent by content, not time. Compare `sha256`." | The philosophy argues size + `ETag` already answers idempotency; a client-side sha256 pass is redundant. |
| `.agents/rules/datapack.md` | "Verification compares counts, byte sizes, and ETags" | Already aligned — this is the mechanism the philosophy prefers. |

The architecture note also lists sha256 for a "cache key"; that use does not survive, since
CloudFront keys on the URL. The one use still open is **deduplication within a dataset**:
the note records `dup_sha256` as a violation counter, but the sample is 10 rows and whether
real duplicates exist across the full dataset is unmeasured. First resolved by keeping
`sha256` only for within-dataset deduplication, then superseded the same day — see
"Zero-hash decision" below.

## Zero-hash decision (supersedes the addendum)

Asked whether the gateway needs a hashing capability at all, first principles said it does
not — and never had one: `gateway/` contains no hash code and its rules never mention any,
and the architecture note mentions `sha256` exactly once (the datapack manifest's
`violations.dup_sha256` counter; Chinese 哈希 never appears). The gateway's only auth is
`Authorization: Bearer <key>` compared against keys held in SSM/env.

Rather than keep the sole surviving exception (within-dataset deduplication), the project
dropped hashing entirely:

- `project.md` gains a repo-wide invariant: no hashing capability; identity comes from names,
  keys, and platform tokens (`ETag`, S3 native checksums, image digests, object versions,
  upstream revisions).
- `datapack.md` loses the dedup bullet and dedups neither within nor across datasets;
  byte-duplicates are a source-data-quality matter.
- `deploy.md` rollback pins identity (tag / image digest / upstream revision / object
  version) instead of a `SHA256SUMS` file we maintain.
- `datapack/sources.yaml` drops the `digest` field; an immutable revision goes in `upstream`.
- `design-philosophy.md` dedup row moves to "No".

## Decision log (H1–H4)

| # | Decision |
|---|---|
| H1a | Rollback pins identity, not a self-computed checksum. |
| H1b | Withdrawn — superseded by the zero-hash decision: no dedup, no `sha256` anywhere. |
| H2 | **Reversed the earlier call**: `docs/` does commit a `uv.lock`, like gateway and datapack. `zensical==0.0.63` relaxed to `>=0.0.63`; the lock holds the exact version. |
| H3 | Error contract frozen: `ErrorResponse {error:{code,message}}` plus the initial `ErrorCode` set; the validation handler now builds the model. |
| H4 | API-reference generation belongs to the gateway environment; `docs/` only renders committed markdown. |

Verified after the change: gateway `ruff`/`mypy`/`pytest` (3 contract tests) clean; datapack
`ruff`/`mypy` clean; `docs/uv.lock` generated (13 packages).

Audit drift (M1–M5) was cleared in a follow-up pass: `deploy.md` no longer asserts a
`gateway/Dockerfile` or `.env.example` exists; `skills/` and `.github/workflows/` are marked
planned in AGENTS.md/README; the README tree matches AGENTS.md; and the README notes the
gateway skeleton and the unimplemented `gwhs-datapack` CLI. (M2 was already cleared by the H2
reversal.)

Backend abstraction: the GPU compute plane is now documented as an abstract resource — any
Docker-capable GPU backend reachable over an authenticated, non-public link (lab machine, GPU
VPS, rented container) — instead of one hard-coded lab route with named machines and a named
overlay network. The security invariant is kept (backends are never publicly reachable and
carry their own token); the transport is an implementation detail. `gateway.md` additionally
forbids special-casing a provider behind the uniform backend protocol.

Package rename: `datapack/` became `datasets/`, with dist/module/CLI `biohack-datasets` /
`biohack_datasets` / `biohack-datasets`, because `gwhs` names one of the three datasets and was
wrong as the project namespace. The other two dist names became `biohack-gateway` /
`biohack-docs`, the FastAPI title became `Global Wheat API`, and both document titles became
`Global Wheat …`. The CLI flag `--version` became `--release` (it collided with the universal
print-version convention). All three `uv.lock` files were regenerated. Earlier text above keeps
the old name as history.

Gateway packaging: `gateway/` stopped being a flat, unpackaged project (`app.py` at the root,
`package = false`) and became an installable package under `src/biohack_gateway/`, launched as
`uvicorn biohack_gateway.app:app`. The trigger was concrete: Python puts a *script's own
directory* on `sys.path[0]`, so a script in `gateway/scripts/` could not `from app import app`
(verified: `uv run python tests/test_error_contract.py` → ModuleNotFoundError, while
`uv run python -c "import app"` worked). Packaging removes the CWD dependence for the app, the
systemd unit, and the future docs generator; `pythonpath = ["."]` was dropped and mypy now
scans `src`.

API-reference generator: a survey of standalone OpenAPI→Markdown converters (2026-10) found
none that fits without adding cost. `openapi-markdown` (Python, Jinja2, last release 2025-02)
and `openapi-to-md` (Python, `0.1.0b2`, essentially dormant) both emit a single fixed-shape
file; `widdershins` (Node, 1.6k★) emits (Re)Slate-specific markup and its per-tag split is an
unmerged issue; `openapi-generator` needs a JVM; the web converters are network services.
None handles what the prototype encodes: suppressing FastAPI's auto-422 in favour of our 400
contract, `x-doc-example`, `$BASE`/`$GWHS_API_KEY` curl examples, and per-tag pages with the
handwritten `errors.md`. Decision: keep the hand-rolled `gateway/scripts/gen_api_docs.py`, and
record this survey so the "reuse first" rule is satisfied with evidence rather than assertion.
Only credible reuse path if ever revisited: `openapi-markdown`'s traversal with our own
Jinja template.

SiYuan sync: the architecture note (`20261008215136-w5dm68m`, "黑客松API服务架构设计") was
reverse-updated to match the repo: §七 renamed to 数据集发布工具（datasets）, the CLI example to
`biohack-datasets --release`, zero-hash wording throughout (idempotency by size + ETag, no
content checksum, no `dup_sha256`, no `SHA256SUMS`), the error body to
`{"error":{"code","message"}}`, docs now committing a `uv.lock`, the API-reference section to
generated markdown (no Redoc/Scalar page, no release asset) plus the converter survey, the
repo tree to `datasets/` + `gateway/src/biohack_gateway/`, and the GPU plane to abstract
backends (any Docker-capable resource over an authenticated non-public link). The note's §二/§九
measured machine facts were intentionally kept — they are measurements, not the hardcoded
route. Known cosmetic residue: the mini-gateway sample still returns `backend="home-pc-4090"`.

## Open items

- `gateway/scripts/gen_api_docs.py` is not written; the layout is planned but the script is
  still only in the architecture note. Before writing it, apply the reuse rule and check for
  an existing OpenAPI→Markdown converter.
- Zensical site configuration (`zensical.toml`) does not exist, so `zensical serve` /
  `build` are documented but not yet runnable.
- Nothing is committed yet; the working tree holds all new files.
- `docs/data-layout.md` is a placeholder pending the `site` / `stage` field decision.
- Duplicates are no longer detected anywhere; if source-quality issues appear, they are
  reported by the source, not resolved by the publisher.
