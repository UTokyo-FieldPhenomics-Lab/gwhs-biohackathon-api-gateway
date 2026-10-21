# Datasets Rules

Package: `datasets/`. Shared constraints are in [`project.md`](project.md).

- **Keep `fetch` separate from one complete `publish` operation.** Fetch populates the pinned cache;
  publish builds, validates, uploads, verifies and installs both image and metadata artifacts.
  `--dry-run` writes nothing; any failure exits non-zero, and only success permits downstream teardown.
  [Publisher](../../datasets/src/biohack_datasets/publish.py).
- **Read serially and upload with bounded concurrency, without staging images.** Preserve the
  parquet row-group memory bound; parallel readers would multiply it. Upload row bytes directly so
  disk holds the upstream cache and metadata index, not a second image tree.
  [Streaming](../references/202610131500_data-plane-region-and-streaming.md).
- **Attempt conditional creation before reading object metadata.** `If-None-Match: *` prevents
  overwrite. Verify a new PUT with HEAD size, opaque ETag, Content-Type and Cache-Control. On an
  occupied key, skip only when size and the two owned headers match; otherwise stop with the key.
  No automatic header repair or unconditional write. A denied HEAD never means absence.
  [Simplification](../references/2026102115_documentation-and-implementation-alignment.md).
- **Resumability trusts the same pinned upstream bytes, not a content comparison.** Do not hash,
  trust mtimes, detect duplicates or promise equality from matching lengths and headers. ETags come
  from the store; a test double supplies opaque tokens, not MD5.
  [Decision](design-philosophy.md), [tests](../../datasets/tests/test_publish.py).
- **Use one single-part PUT per image or mask.** Keep objects within that operation's size limit;
  do not add multipart machinery to this small-object publisher.
  [Object-store interface](../../datasets/src/biohack_datasets/s3.py).
- **Every object has its actual content type and an immutable cache policy.** Set Content-Type from
  the detected file extension and `Cache-Control: public, max-age=31536000, immutable`; browsers and
  caches must not need another source for those facts. [Formats](../../datasets/src/biohack_datasets/formats.py).
- **Object paths are write-once and the publisher never deletes.** Reject build-time key collisions;
  changed content needs a new dataset id/path. A deliberate wipe of a development prefix is a human
  action, never an automatic recovery path. [Layout](../../docs/data-layout.md).
- **Copy upstream image bytes verbatim.** Header inspection and filename normalization are allowed;
  decoding/re-encoding is not. Raster mask files are objects; COCO RLE geometry stays in the index.
  [Layout](../../docs/data-layout.md).
- **Number rows absolutely across sorted upstream parquet paths.** Use zero-based cumulative rows,
  eight-digit padding only in object names, and carry upstream ids as metadata, not another address.
  [Identity](../../docs/data-layout.md).
- **Build one unpublished SQLite index per dataset and replace it atomically.** Stage beside the
  destination in host state and `os.replace` only after upload verification; failure preserves the
  prior index. Index transport to the gateway is an operation, not another publisher contract.
  [Index implementation](../../datasets/src/biohack_datasets/index.py).
- **A class vocabulary is pinned provenance.** Read classes and palettes from the full-commit HTTPS
  URL in `sources.yaml`, not code constants or paper figures. Exclude background from present-class
  sets. [Vocabulary](../../datasets/src/biohack_datasets/labels.py).
- **Publish one artifact per purpose.** Do not mirror redundant source archives or deduplicate rows
  within/across datasets. One adapter serves one schema; schema-sharing ids retain separate prefixes
  and indices. Missing adapters fail explicitly. [Sources](../../datasets/sources.yaml).
- **Keep output content deterministic.** Fix iteration/insertion order and numeric formatting;
  compare ordered SQLite content, not database-file bytes whose page layout can vary. Counts remain
  queries, not independently stored facts. [Index tests](../../datasets/tests/test_index.py).
- **Only fetch may populate the disposable upstream cache.** Rebuild from immutable revisions;
  never hand-place cache files or commit them. Refuse incomplete selected shards to preserve row
  identities, including for limited rehearsals. [Cache tests](../../datasets/tests/test_fetch_cache.py).
- **`include` controls the fetched selection and stays under `data/`.** Narrow it in `sources.yaml`
  instead of fetching source-root archives/cards. Keep upstream locations, adapters, vocabularies
  and distribution licenses in that file; clear licensing before public release.
  [Source validation](../../datasets/src/biohack_datasets/sources.py).
- **Keep heavy dependencies here.** PyArrow, Pillow and boto3 must not enter the gateway environment.
  [Package boundary](../../datasets/pyproject.toml).
