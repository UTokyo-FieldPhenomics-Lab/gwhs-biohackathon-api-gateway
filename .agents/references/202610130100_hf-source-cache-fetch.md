# Upstream Fetch Step and the GWFSS Duplicate

Date: 2026-10-13 01:00 JST
Topic: implementing `biohack-datasets-fetch` — the step that materialises the pinned upstream
parquet into `datasets/.cache` — and what verifying the GWFSS upstream actually showed.

## Objective

Let a host build the publisher's input without downloading the data by hand, and without the
~1.8 GB of redundant bytes the GWFSS repository offers under three file-name prefixes. The step
runs on the API host, which is where a publish runs and therefore where the cache lives.

## What was done

1. Added `datasets/src/biohack_datasets/sources.py`: the reader for `sources.yaml` — one
   `Source` per dataset, holding an `Upstream` (repo, commit sha, include patterns) or `None`.
2. Added `datasets/src/biohack_datasets/fetch_cache.py`: `plan` (list what an upstream would
   put in the cache, sizes included), `materialise` (download), `verify` (read every parquet
   footer back), and the `biohack-datasets-fetch` CLI.
3. Added `include:` to `sources.yaml`, defaulting to `data/*.parquet`; `gwfss` narrows it.
4. Added `huggingface-hub>=2.2` and the second console script; `datasets/.cache/` is named in
   `.gitignore` (already covered by the generic `.cache` rule).
5. Updated `project.md`, `deploy.md`, `datasets.md`, `deploy/README.md`, `README.md`, and
   `AGENTS.md` where "publishing does not happen on the API host" no longer holds.

## The GWFSS duplicate, measured

`GlobalWheat/GWFSS_v1.0` lists six parquet files under `data/` at the pinned revision. Three
prefixes — `all-`, `full-`, `labelled-` — turn out to be the same two files. The evidence, all
read with range requests, so the check cost about 50 KB rather than 1.8 GB:

| Evidence | `all-00000` | `full-00000` | `labelled-00000` |
|---|---|---|---|
| tree API `lfs.oid` | `bcfe0585…444f` | `bcfe0585…444f` | `bcfe0585…444f` |
| `resolve` `x-linked-etag` | `bcfe0585…444f` | `bcfe0585…444f` | `bcfe0585…444f` |
| parquet footer bytes | identical, 16155 B | identical | identical |
| size / `num_rows` | 258313428 / 548 | same | same |

The second shard agrees the same way (352455102 bytes, 16952-byte footer, 548 rows), and
548 × 2 = **1096**, which is the number the dataset card declares for `labelled`. The Hub's
`datasets-server` also registers `labelled` as the only split. The files carry `image` and
`mask`, so the claim "only `labelled` is annotated" is false: the three names are byte-identical,
mask included. If `all` was meant to add the unlabelled images, the upload never did — those live
at the ETH research collection, not on the Hub.

**Lesson.** An upstream's file names are a claim, not a fact. Pinning the revision makes the
bytes immutable, and the platform's own tokens (`lfs.oid`, `x-linked-etag`) plus a byte comparison
of the two footers settle what a name cannot — with no digest computed on our side anywhere.

## Decisions and their reasons

| Decision | Reason |
|---|---|
| A separate `biohack-datasets-fetch` script, not a subcommand of `biohack-datasets` | The publisher's contract is one command with four switches that must not be split; fetching is a different job on a different machine |
| `huggingface_hub`, not hand-rolled HTTP | Redirect handling, resumption, retries, and per-file size checks are the library's; a hand-rolled client would reimplement them and get the `Authorization`-across-redirect case wrong |
| `snapshot_download(dry_run=True)` as the plan | The library already answers "what would be fetched, at what size, and what is cached"; a second listing implementation would drift from it |
| Verify by reading parquet footers, not by hashing | `project.md` forbids hashing capability, and the Hub's client already checks sizes; what it cannot check is that the bytes are parquet, which is exactly what the publisher needs |
| An unreadable file is deleted, and the run fails | The Hub client would otherwise keep believing the cache entry is present and skip it on the rerun |
| Patterns stay under `data/`, and a test enforces it | The upstreams keep a 611 MB zip of their own parquet at the root; nothing must ever fetch it |
| No disk-space pre-check | The cache is 1.88 GB against a 37 GiB free root volume, and both modes print the planned and missing byte counts, so the number is visible before it is spent |

## Verified

- `cd datasets && uv run pytest` → 14 passed, 4 deselected (offline).
- `uv run pytest -m network` → 4 passed: the pinned plan lists exactly 4 / 2 / 1 parquet files
  for `gwhd` / `gwfss` / `gwhs-temp`, and a fetch narrowed to `README.md` downloads exactly one
  1846-byte file and no parquet.
- `uv run mypy` and `uv run ruff check .` clean. `mypy` needed `types-pyyaml`, and an override
  for `pyarrow`, which ships no stubs.
- `uv run biohack-datasets-fetch --dry-run` reports `gwhd` 1.26 GB, `gwfss` 610.8 MB,
  `gwhs-temp` 11.4 MB, and skips `gwhs`.

## Open items

- The full 1.88 GB fetch has not been run yet; it belongs on the API host.
- The publisher still reads no cache — wiring `biohack-datasets` to `datasets/.cache` is the
  next step, and it is where the row counts `verify` already prints become the index.
- A `--dry-run` still creates the cache directory and empty `.lock` files, because the Hub client
  does that while listing. No bytes, 100 KB of directories; not worth working around.
