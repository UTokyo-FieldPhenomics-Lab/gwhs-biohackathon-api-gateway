# Datasets Rules

Package: `datasets/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **One command, two subcommands.** `biohack-datasets fetch` fills the local cache from the
  pinned revisions; `biohack-datasets publish` runs build → validate → upload → verify and
  emits both artifacts: image objects to S3 and one metadata index per dataset for the gateway
  host. Splitting a publish into stages invites skipping one, and `--dry-run` prints the plan
  and writes nothing.
- **Exit code is the only teardown signal.** A non-zero exit stops the pipeline; only exit 0
  authorizes tearing down whatever environment the run set up, so validation cannot be ignored.
- **Objects are streamed, never staged.** A publish reads a few rows at a time out of the cached
  parquet and PUTs each object straight to S3, so peak disk is the cache plus nothing and no
  scratch volume exists. Peak memory is one parquet row group — the largest measured is 108 MB —
  and that is a ceiling only while reads stay serial: concurrency belongs to the upload side, and
  parallel readers are what would multiply it.
- **Idempotent by comparison, not timestamps.** An object is already correct when its byte size
  and the two headers the publisher owns (`Content-Type`, `Cache-Control`) match what this run
  would write — one `HeadObject` returns all three. Equal size with different headers is repaired
  by re-`PUT`ting the same bytes: the content did not change, so it is not an overwrite. A
  different size stops the run and names the key. Do not hash locally and do not trust mtimes:
  the entity tag is used only after an upload, comparing the `PUT`'s answer with a `HEAD`, and is
  never a value the publisher computes. The comparison is about objects in the publisher's own
  prefix, never about files on the machine: code identity is a git tag or commit, dependencies
  are `uv.lock`, data identity is the pinned upstream revision, and none of the three is ever
  verified by comparing bytes.
- **Validate before upload, verify after.** Metadata is validated before it is written; image
  objects are verified after upload by comparing counts, byte sizes, and ETags. Because a
  single-part `PUT` keeps `ETag` equal to the content MD5, that verification is cheap and
  always runs. Every object is small enough for a single-part `PUT` — the largest measured is
  about 4.1 MB (an RGBA PNG) — and that is load-bearing: a multipart upload would replace the
  content MD5 with a digest of the parts and void the comparison.
- **Every object carries its own type and a long cache lifetime.** `Content-Type` follows the
  file extension, because S3 stores no type of its own and a browser will not display an object
  typed as `binary/octet-stream`; `Cache-Control: public, max-age=31536000, immutable` follows
  from a path being written once, and without it a viewer revalidates on every request even
  though CloudFront holds the object.
- **No content hashing.** The publisher computes no checksum and stores none; correctness
  rests on `size + ETag`. Byte-duplicate rows are a source-data-quality matter — they are
  neither detected nor resolved here. See [`design-philosophy.md`](design-philosophy.md).
- **S3 images are frozen at publish.** An object path is written once and never overwritten;
  changed content means a new path. There is no version prefix, and collisions raise at build
  time — including the collision a re-publish meets from the other side, a key that already exists
  and would receive a different byte sequence: the run stops and names that key, and the operator
  decides between a new dataset id and a deliberate wipe of a prefix still under development. The
  publisher never overwrites and never deletes; both are human actions.
- **Bytes are copied verbatim out of the parquet column.** Extracting an image is a byte copy,
  never a transcode: never decode-and-re-encode. Reading a header for its dimensions is fine, and
  normalizing the filename is fine; nothing else touches the bytes, because the upstream encoder
  is the only one that ever runs. Annotation bytes are not automatically objects, nor the
  reverse: object geometry the upstream published as COCO RLE is carried as a value in the
  metadata index and is not an object, while a mask the upstream shipped as an image is an
  object and is byte-copied like one.
- **The row number is the identity.** Identity is the absolute row number of the pinned upstream
  revision, zero-padded to **eight digits** in an object name and never in an address. It is the
  zero-based cumulative index over the dataset's parquet files in sorted path order, which is what
  makes it deterministic. An upstream id, where one exists, is carried as a field and never as a
  second way to address the same row.
- **The metadata index is one SQLite file, and it is not published.** One `<dataset>.db` per
  dataset, written to the host state directory (`~/.config/biohack-gateway/index`) rather than
  into the checkout. It is built beside its target and moved into place with `os.replace`, so a
  reader never sees a half-written file. The index file travels, the images never do, and how it
  reaches the gateway is a deployment detail.
- **A class vocabulary is provenance.** Where a mask annotation has named classes and a palette,
  they are pinned in `sources.yaml` as `<repo>@<sha>#<path>` and read from there into the index.
  Never a constant in code, and never a colour read off a paper figure: the stored palette is the
  only authority.
- **One artifact per purpose.** Never publish the same bytes twice; an upstream release that
  ships a redundant archive beside its data is not republished in kind.
- **Deterministic output.** Fixed insert order, fixed float formatting, and fixed iteration
  order, so a republish is reproducible rather than merely repeated. For a SQLite artifact the
  guarantee is **content** determinism — a canonical ordered read-back — and not byte equality,
  because SQLite's page layout is version-dependent. Counts are queries, not stored facts.
- **One adapter per schema, one prefix per dataset.** GWHD, GWFSS, and GWHS each have a reader;
  dataset ids that share a schema share the reader but never the prefix or the index. An adapter
  that is not implemented is a hard error naming it, never a silent skip. No deduplication
  happens within or across datasets. A preliminary or partial release takes its own dataset id,
  so that a later full release never renumbers an address already in use.
- **`sources.yaml` is the provenance record.** Upstream locations — carrying an immutable
  revision — adapters, class vocabularies, and licenses live there; a license must be cleared
  before public distribution.
- **The cache is fetched, never hand-placed.** `datasets/.hf_cache/<dataset_id>/` is produced by
  `biohack-datasets fetch` from the pinned revision in `sources.yaml`, holds upstream bytes
  verbatim, is disposable and machine-local, never enters git, and is rebuilt rather than
  preserved. Nothing is copied into it by hand.
- **`include` decides what leaves an upstream.** Only files matching a dataset's patterns are
  fetched, and the patterns stay under `data/`: an upstream that keeps an archive or a card
  beside its data has it fetched never, because its bytes are already there once. A dataset
  that needs a narrower selection than the default says so in `sources.yaml`.
- **Heavy dependencies live here.** `pyarrow`, `Pillow`, and `boto3` belong to this package
  and must never leak into the gateway's dependency set.
