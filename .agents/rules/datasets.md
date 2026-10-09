# Datasets Rules

Package: `datasets/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **One command, four switches.** A single invocation does build → validate → upload → verify;
  splitting build from sync invites skipping a stage.
- **Exit code is the only teardown signal.** A non-zero exit stops the pipeline; only exit 0
  authorizes destroying the temporary instance, so validation cannot be ignored.
- **Idempotent by comparison, not timestamps.** Decide whether an object is already correct
  from its byte size plus its MD5 (`ETag` for a single-part `PUT`). Do not hash locally and
  do not trust mtimes.
- **Validate before upload, verify after.** Verification compares counts, byte sizes, and
  ETags; because a single-part `PUT` keeps `ETag` equal to the content MD5, it is cheap and
  always runs.
- **No content hashing.** The publisher computes no checksum and stores none; correctness
  rests on `size + ETag`. Byte-duplicate rows are a source-data-quality matter — they are
  neither detected nor resolved here. See [`design-philosophy.md`](design-philosophy.md).
- **Immutability in practice.** A published version prefix is never overwritten, collisions
  raise at build time, and a new version goes to a new prefix.
- **Bytes are copied verbatim.** Extract images straight from the source; never decode and
  re-encode. Normalize filenames only.
- **Deterministic output.** Fixed JSON key order, float formatting, and iteration order, so
  dev output is byte-comparable with prod.
- **One adapter per dataset.** GWHD, GWFSS, and GWHS get separate adapters, top-level
  prefixes, and manifests. No deduplication happens within or across datasets.
- **`sources.yaml` is the provenance record.** Upstream locations — carrying an immutable
  revision — and licenses live there; a license must be cleared before public distribution.
- **Heavy dependencies live here.** `pyarrow`, `Pillow`, and `boto3` belong to this package
  and must never leak into the gateway's dependency set.
