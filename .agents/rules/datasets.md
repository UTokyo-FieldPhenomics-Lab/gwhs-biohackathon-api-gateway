# Datasets Rules

Package: `datasets/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **One command, four switches, two artifacts.** A single invocation does build → validate →
  upload → verify and emits both outputs: image objects to S3 and a metadata index for the
  gateway host. Splitting the run invites skipping a stage.
- **Exit code is the only teardown signal.** A non-zero exit stops the pipeline; only exit 0
  authorizes tearing down whatever environment the run set up, so validation cannot be ignored.
- **Idempotent by comparison, not timestamps.** Decide whether an object is already correct
  from its byte size plus its MD5 (`ETag` for a single-part `PUT`). Do not hash locally and
  do not trust mtimes.
- **Validate before upload, verify after.** Metadata is validated before it is written; image
  objects are verified after upload by comparing counts, byte sizes, and ETags. Because a
  single-part `PUT` keeps `ETag` equal to the content MD5, that verification is cheap and
  always runs.
- **No content hashing.** The publisher computes no checksum and stores none; correctness
  rests on `size + ETag`. Byte-duplicate rows are a source-data-quality matter — they are
  neither detected nor resolved here. See [`design-philosophy.md`](design-philosophy.md).
- **S3 images are frozen at publish.** An object path is written once and never overwritten;
  changed content means a new path. There is no version prefix, and collisions raise at build
  time.
- **Bytes are copied verbatim out of the parquet column.** Extracting an image is a byte copy,
  never a transcode: never decode-and-re-encode. Reading a header for its dimensions is fine, and
  normalizing the filename is fine; nothing else touches the bytes, because the upstream encoder
  is the only one that ever runs.
- **The row number is the identity.** Identity is the absolute row number of the pinned upstream
  revision. An upstream id, where one exists, is carried as a field and never as a second way to
  address the same row.
- **The metadata index is a build artifact, not a published object.** The gateway loads it from
  its own host, and how it gets there is a deployment detail: the index file travels, the images
  never do.
- **One artifact per purpose.** Never publish the same bytes twice; an upstream release that
  ships a redundant archive beside its data is not republished in kind.
- **Deterministic output.** Fixed JSON key order, float formatting, and iteration order, so
  dev output is byte-comparable with prod.
- **One adapter per schema, one prefix per dataset.** GWHD, GWFSS, and GWHS each have a reader;
  dataset ids that share a schema share the reader but never the prefix or the manifest. No
  deduplication happens within or across datasets. A preliminary or partial release takes its own
  dataset id, so that a later full release never renumbers an address already in use.
- **`sources.yaml` is the provenance record.** Upstream locations — carrying an immutable
  revision — and licenses live there; a license must be cleared before public distribution.
- **The cache is fetched, never hand-placed.** `datasets/.cache/<dataset_id>/` is produced by
  `biohack-datasets-fetch` from the pinned revision in `sources.yaml`, holds upstream bytes
  verbatim, is disposable and machine-local, never enters git, and is rebuilt rather than
  preserved. Nothing is copied into it by hand.
- **`include` decides what leaves an upstream.** Only files matching a dataset's patterns are
  fetched, and the patterns stay under `data/`: an upstream that keeps an archive or a card
  beside its data has it fetched never, because its bytes are already there once. A dataset
  that needs a narrower selection than the default says so in `sources.yaml`.
- **Heavy dependencies live here.** `pyarrow`, `Pillow`, and `boto3` belong to this package
  and must never leak into the gateway's dependency set.
