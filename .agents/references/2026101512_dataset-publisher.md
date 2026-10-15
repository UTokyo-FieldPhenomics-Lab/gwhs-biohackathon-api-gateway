# Dataset publisher: `gwhs-temp` end to end

Date: 2026-10-15. Plan: [`../plans/2026101518_dataset-publisher.md`](../plans/2026101518_dataset-publisher.md).
Discussion that produced the plan: opencode session `ses_ec698ce96ffetb2pVuQDTQsB0z`.

## What was implemented

The whole publish path for `gwhs-temp` (10 rows), on the adapter `gwhs` will reuse. `gwfss` and
`gwhd` adapters are **not** in this item: their names are declared in `sources.yaml`, and
`get_adapter` raises `AdapterNotImplemented` naming them, which is the hard-error rule rather than
a silent skip.

New modules under `datasets/src/biohack_datasets/`:

| Module | Responsibility |
|---|---|
| `model.py` | neutral `Row`/`Annotation`/`Rle` the adapter produces and the index consumes |
| `formats.py` | magic sniffing (`jpg`/`png`), content types, the one `Cache-Control` |
| `adapters/__init__.py` | registry; unimplemented name is a hard error |
| `adapters/gwhs.py` | GWHS reader: file-level `huggingface` ClassLabel metadata, per-row extension, verbatim RLE |
| `index.py` | eight-table SQLite schema, validation, atomic build, canonical read-back |
| `s3.py` | two-method protocol, boto3 implementation, in-memory fake |
| `publish.py` | plan → build → validate → upload → verify orchestration |
| `cli.py` | one command, two subcommands (`fetch`, `publish`) |

`fetch_cache.py` lost its standalone CLI and gained `fetch_datasets`; the
`biohack-datasets-fetch` console script is gone. `sources.py` now parses `adapter`, `license`, and
`labels` (a pinned `<repo>@<sha>#<path>` reference).

## Measured, not assumed

```
rows 10  objects 199  rle 199  tags 61  image bytes 11,265,504  exts {'jpg','png'}
```

The RLE decoder in `docs/examples/rle_decode.py` reproduces the upstream `area` **199/199** and
the upstream `bbox` **199/199**. The JavaScript port was executed against the same counts string
and reproduces area 12074 and bbox `[231, 923, 356, 1084]` exactly.

## Commands run

```bash
cd datasets
uv run pytest                                    # 61 passed
uv run mypy                                      # success, 11 source files
uv run ruff check . && uv run ruff format --check .
uv run pytest -m network                         # 5 passed (downloads the 11.4 MB sample)
uv run biohack-datasets fetch --dataset gwhs-temp --dry-run
uv run biohack-datasets publish --dataset gwhs-temp --dry-run
uv run biohack-datasets publish --dataset gwhs-temp --index-dir /tmp/gwhsidx
sqlite3 /tmp/gwhsidx/gwhs-temp.db 'select count(*) from rows; select count(*) from objects;'
```

## What broke, and the lesson

- **The DSH sandbox blocks `~/.cache/uv`.** `uv run` wrote there and failed with `Read-only file
  system`. Fix: `UV_CACHE_DIR` and `HF_HOME` into `datasets/.cache/` (already gitignored by the
  `.cache` rule). No sandbox escalation was needed.
- **`sources.py` passed the whole YAML entry as `include`.** The first real fetch planned
  `allow_patterns=['adapter','upstream','license']`. Caught by the real `fetch --dry-run`, fixed to
  `entry.get("include")`.
- **A relative `--cache-dir` broke `verify`.** `file.local_path` from `snapshot_download` is
  absolute while `local_dir` was relative, so `relative_to` raised. `fetch_datasets` now resolves
  the cache root; the CLI default was absolute already.
- **COCO RLE is column-major.** Reading it row-major gave area 199/199 but bbox 0/199: run lengths
  and therefore the count were right, positions were not. Both the example decoder and its docs
  now state `index = row + column * height`; the bbox check is what caught it, which is why it
  exists.
- **`VACUUM` cannot run inside a transaction.** The build commits the inserts first.
- **boto3 has no `py.typed`.** Added a `ignore_missing_imports` override next to pyarrow's.
- **Plan vs rule on the skip comparison.** The plan asks to compare `ETag` before skipping, but
  `datasets.md` forbids hashing locally and the plan's own verify section says the same. Resolved
  in favour of the rule: the skip compares size and the two owned headers; the entity tag is only
  compared after an upload, between the `PUT` response and a `HEAD`. A same-size, different-bytes
  object is therefore not detected; a different size stops the run and names the key. The rule was
  rewritten to state exactly this.
- **Generating files through a JS template literal over-escaped backticks.** A triple-escaped
  backtick landed in the files as a backslash followed by a backtick. Lesson for the tooling, not
  the project: grep each generated file for a backslash before a backtick, or avoid them.

## A concurrent writer

The referenced opencode session was still active while this ran and wrote the credential
documentation: `deploy/README.md` §5, a paragraph in `README.md`, and `datasets/.env.example`. It
explicitly noted this implementation in flight. Those edits were preserved and reconciled (one
duplicated §4 table row removed; the README Datasets section, which it left on the old
`--release` interface, was rewritten here). No code file was touched by both.

## Host-only acceptance still open

The seven checks at the top of the plan need the host, the bucket, and the AWS profile; none could
run here (no `aws` CLI, no bucket). Numbers 2–6 (upload, `data.*` headers, CloudFront `x-cache`,
private-bucket `403`, `cmp` of one JPEG and the 4 MB PNG, second run skipping) are unverified.
Number 1, number 7, and the local index build were verified, and the index read back 10 rows /
199 objects / 199 RLE / 61 tags with names resolved.

## Deferred, with triggers, as the plan records

`gwfss` and `gwhd` adapters; the `class.json` loader behind `row_classes`; rendering `gwhs`
instance masks; transcoding `gwfss` masks; a published bulk export; an `?include=` parameter.
Executing `docs/examples/` in CI waits on the planned `.github/workflows/`, which does not exist
yet; `rle_decode.py` was executed here (199/199) and `rle_decode.js` cross-checked by hand.

## Follow-up: the credential record is the console CSV

The credential design was simplified after review. `publish/` no longer holds a converted
`credentials` INI: the console's download is placed there directly as `publish/awsAccessKeys.csv`
(0600) and the publisher reads it. `biohack_datasets/credentials.py` parses the two columns
(`Access key ID`, `Secret access key`), tolerating the byte-order mark and CRLF a browser download
produces, and never puts a value in an error. `cli._publish_session` hands the pair straight to a
boto3 session, so the secret still never enters the environment. `publish/env` therefore carries
three names -- `BIOHACK_BUCKET`, `AWS_REGION`, `BIOHACK_ACCESS_KEYS_CSV` -- instead of four, and
`setup-gateway.sh` creates `publish/` up front so the unit's `InaccessiblePaths=` always resolves
(systemd refuses a unit whose excluded path does not exist). The state-directory tree is now
documented in `deploy/README.md` §1.5. All gates stayed green (71 tests).

## Follow-up: HeadObject 403 under the no-ListBucket policy

The first real publish on the host failed with `403` from `HeadObject` on the first key. The
publish policy grants `s3:PutObject` and `s3:GetObject` and no `s3:ListBucket`, and a private
bucket answers `403` — not `404` — for a key that does not exist, because revealing the difference
would leak existence. The publisher read that as a hard error, so no first publish could start.

Fix: a denied `HEAD` raises `ObjectUnreadable`, which is *not* evidence of absence; the publisher
settles existence with a conditional create (`PutObject` with `If-None-Match: *`). The create
succeeds only if the key is free (`uploaded`), or is refused with `412` (`AlreadyExists`) when it is
occupied, after which the normal size and header comparison decides `skipped` or `repaired`.
Existence is never assumed from a denial and nothing is overwritten, so the write-once rule holds
without granting `ListBucket`. Tests cover the 403, the 412, the lost-race fallback, and the memory
fake's conditional create; the rules, the deploy README, and the plan record the nuance.
