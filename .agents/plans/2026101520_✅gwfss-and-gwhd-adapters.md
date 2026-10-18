# gwfss and gwhd readers, and the vocabulary as a URL

Status: **done** (implemented 2026-10-15; committed and published since). Both adapters live in
`datasets/src/biohack_datasets/adapters/`, the published objects answer
(`https://data.global-wheat.com/gwfss/images/00000004.png` was fetched for a model run on 2026-10-18),
and the gateway lists both datasets. The record is
[`../references/2026101520_gwfss-and-gwhd-adapters.md`](../references/2026101520_gwfss-and-gwhd-adapters.md).

## Goal and acceptance

Implement the two remaining adapters, `gwfss` (image + semantic mask) and `gwhd` (image +
bounding boxes), so that `gwhs`, `gwhs-temp`, `gwfss` and `gwhd` all have a reader and the
registry no longer reports them unimplemented. Acceptance: a local `publish --index-dir` builds an
index whose `classes`, `row_classes`, `mask_ext` and row metadata are right, the gateway serves it,
and the offline test suite pins every shape without the Hub.

## Measured facts this plan rests on (the real pinned files, 2026-10-15)

- `gwhd` `data/train-00000-of-00002.parquet`: 1828 rows, 19 row groups. Schema is
  `image: struct<bytes, path>`, four string metadata columns, and
  `objects: struct<boxes: list<list<int64>>, categories: list<int64>>`. **No `width`/`height`
  columns, and no ClassLabel anywhere** — the file-level metadata declares plain `int64` for
  `categories`. First row: 1024² JPEG, 47 boxes `[936, 4, 41, 78]…`, every category `0`.
- `gwfss` `data/labelled-00000-of-00002.parquet`: schema is `image` and `mask` structs only, **no
  `width`/`height`** and no annotation column at all. The mask is an RGB PNG carrying exactly the
  palette colours; the first 20 rows are 512² image and 512² mask.
- The vocabulary is a file in a **model** repository: `…/resolve/<sha>/class.json` answers 347 B of
  JSON; the `/blob/` link is a 139 KB HTML page and `main` is mutable.

## Decisions

1. **`labels` is an immutable URL.** `sources.yaml` records the `resolve/<sha>/` URL of the
   vocabulary file, and `fetch` downloads it with a plain HTTPS GET into
   `.hf_cache/<dataset_id>/labels/`. This removes the repository-type question entirely, and the
   parser enforces the two properties that make it provenance: `https://` and a 40-character commit
   in the URL, with `/blob/` refused because those bytes are a viewer page.
2. **Dimensions come from the image header.** Neither schema publishes them, so both adapters read
   them with Pillow and decode nothing — which `.agents/rules/datasets.md` already allows.
3. **The palette is injected into the reader.** `gwfss` cannot name a row's classes without it, so
   `get_adapter("gwfss")` without a vocabulary is a loud `MissingVocabulary` rather than an index
   with empty classes.
4. **A mask colour outside the palette stops the row.** Background is a class, so nothing is
   expected outside the palette; a mismatch means the mask and its legend disagree, and
   `row_classes` would be a lie.
5. **A cache missing a shard is refused.** `missing_shards` parses `-NNNNN-of-MMMMM` from the file
   names and `publish` stops before reading a row, because a gap moves every later row onto an
   address that is not its own. The cache is therefore fetched whole — about 1.9 GB — which is also
   what makes the full pre-flight possible off the host.

## Files

- new `datasets/src/biohack_datasets/adapters/common.py` — `AdapterError`, `split_of`, header
  dimensions, batch size, shared by every reader.
- new `datasets/src/biohack_datasets/labels.py` — `Palette`, `load_palette`.
- new `datasets/src/biohack_datasets/adapters/gwhd.py`, `…/gwfss.py`.
- `adapters/__init__.py` — the registry gains both, and `MissingVocabulary`.
- `adapters/gwhs.py` — uses the shared helpers instead of its own copies.
- `sources.py` — `Labels` holds a URL; `_parse_labels` validates it.
- `fetch_cache.py` — `labels_path`, `fetch_labels`, and a fetch that reports the vocabulary.
- `publish.py` — loads the palette from the cache and passes `classes` to the index.
- `cli.py` — catches the two new provisioning errors.
- `sources.yaml` — the pinned URL, and a corrected comment.
- docs: `docs/data-layout.md`, `docs/content/examples/label_masks.py`, `.agents/rules/datasets.md`,
  `README.md`.

## Tests

Synthetic parquet for both schemas, built from the measured Arrow types: dimensions from a real
header, per-row extension sniffing, cumulative row numbers, box/category pairing, mask/image size
agreement, palette membership, and every refusal naming its row. Plus a `class.json` reader test
and a fetch test for the vocabulary URL.

## Verification

Offline suite, `mypy`, `ruff`; then a real-file run over the cached
`train-00000-of-00002.parquet` and `labelled-00000-of-00002.parquet`: row counts, extensions,
dimensions, box counts, and for `gwfss` the palette ids found per row.

## Result (2026-10-15)

Guard, full cache, and pre-flight all done locally; nothing committed or uploaded.

- `missing_shards` refuses a selection with a gap, and `IncompleteCache` names the file that is missing.
- The complete selection is cached: gwhd's four files (1.26 GB) and gwfss's two (611 MB), with the
  pinned `class.json` beside them.
- Full local publish, no bucket: `gwhd` 6512 rows / 275 371 objects, splits starting at rows
  0 / 1381 / 5036, every row a 1024² JPEG; `gwfss` 1096 rows / 1096 masks, 987 at 512² and 109 at
  1024², every mask agreeing with its image — the open item below is closed, no mismatch exists.
- `sources.yaml`: `gwhs` takes gwfss's `cc-by-4.0` instead of `null`.
