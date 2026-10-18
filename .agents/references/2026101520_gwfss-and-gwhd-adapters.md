# gwfss and gwhd readers: the mask schema, the box schema, and the vocabulary as a URL

Plan: [`../plans/2026101520_✅gwfss-and-gwhd-adapters.md`](../plans/2026101520_✅gwfss-and-gwhd-adapters.md).
Left uncommitted and undeployed by request; the complete selection is cached locally.

## What was built

- `adapters/gwhd.py` — image, four metadata columns, and boxes. The upstream declares no vocabulary
  at all, so a category is the integer in text.
- `adapters/gwfss.py` — image and semantic mask, with the palette injected.
- `adapters/common.py` — `AdapterError`, `split_of`, `object_bytes`, `image_extension`, and
  `image_dimensions`, shared by all three readers.
- `labels.py` — `Palette`, `load_palette`, and `class_ids_in`.
- `sources.py` — `Labels` holds an immutable URL instead of `<repo>@<sha>#<path>`.
- `fetch_cache.py` — `labels_path`, `fetch_labels` (one HTTPS GET, staged and renamed), and a plan
  line for the vocabulary.
- `publish.py` — `missing_shards` refuses a selection with a gap, and `_palette_of` loads the
  vocabulary from the cache and passes `classes` to the index.
- `cli.py` — catches `CacheFetchError` and `MissingVocabulary`.

## Measured on the real pinned files (2026-10-15)

| | gwhd `train-00000-of-00002` | gwfss `labelled-00000-of-00002` |
|---|---|---|
| rows in the shard | 1828 | 548 |
| schema | `image` struct, 4 strings, `objects{boxes,categories}` | `image` and `mask` structs only |
| width/height columns | none | none |
| ClassLabel metadata | none — `categories` is plain `int64` | n/a |
| first row | 1024² jpg, 47 boxes, all category `0` | 512² png, mask 512² RGB |
| full local publish | 1828 rows, 81 644 objects | 548 rows, palette verbatim |
| class presence | n/a | head 367, stem 407, leaf 547 |

Reading 50 rows took 0.0 s (gwhd) and 0.2 s (gwfss) — the header read and `getcolors` are cheap; the
download dominates.

## Decisions worth remembering

- **The vocabulary is a URL, and the reason is a repo type.** `GlobalWheat/GWFSS_Segformer_b0` is a
  **model** repository: the old reference could not say so, and
  `list_repo_files(..., repo_type="dataset")` answers 404. The URL points at `resolve/<sha>/`; the
  `/blob/` link the Hub's UI offers returns a 139 KB HTML page, so `/blob/` is refused by the parser
  rather than downloaded and failed later. `https://` plus a 40-character commit is the whole check.
- **The background is not presence.** The first `class_ids_in` returned every colour in the mask,
  which is class `0` in every row because the mask's background is painted. That contradicts the
  publisher plan's measured facet (`head 680 / stem 823 / leaf 1096` of 1096 rows, no background) and
  the gateway's committed fixture, so `Palette.class_ids_in` now drops the class the vocabulary names
  `background`. Presence means "annotates something"; a vocabulary that names no background excludes
  nothing. A test I wrote expecting the plan's numbers is what surfaced this.
- **Dimensions come from the image header.** Neither schema has `width`/`height`, and
  `.agents/rules/datasets.md` already allows reading a header; `formats.py` must stay Pillow-free, so
  `image_dimensions` sits in `adapters/common.py`. Bytes are still copied verbatim.
- **The palette is injected into the reader.** Presence cannot be computed without it, so
  `get_adapter("gwfss")` without one raises `MissingVocabulary` rather than building an index whose
  `classes` is silently empty. `check_adapter` exists separately so `--dry-run` can validate the
  name without needing a vocabulary that a dry run would not use.
- **Sniff before reading the header.** `image_dimensions(b"GIF89a")` raises `AdapterError` before the
  magic can be named, so both readers call `_extension` first. A test for "unknown magic stops and
  names the row" is what caught the ordering.

## Simplification pass (same day, before any commit)

Reviewed for redundancy and over-defence, then applied:

- three never-firing `except` branches removed — `UnidentifiedImageError` and `HTTPError` are both
  `OSError` subclasses (checked with `issubclass`, not assumed), so those tuples only ever took one
  path;
- the per-reader `_extension`, `_image_bytes` and `_object_bytes` copies collapsed into
  `common.image_extension` and `common.object_bytes`, including the third inline copy in `gwhs`;
- `Palette` stopped storing the palette twice: the rgb tuple was a parallel array that had to stay in
  step with the hex classes, so `id_for_colour` now renders the input pixel back into `#rrggbb` and
  compares strings;
- `_require_vocabulary(palette, name)` became `_gwfss(palette)` — the name never varied — and the
  factories spell their ignored palette as `lambda _:`;
- `gwhd`'s non-dict `objects` guard used to **return an empty annotation silently**, which would have
  published "this image has no boxes" from a schema drift; it now raises `AdapterError` naming the type;
- `formats.sniff_image_ext`'s message lost its leading "image", because the wrapper now names the
  object and "image: image magic" was redundant.

Kept deliberately: `check_adapter` (the only way `--dry-run` can validate an adapter name without a
vocabulary, and a dry run must not require a fetch), `MissingVocabulary` (a mask reader without its
palette would otherwise build a silently empty `classes` table), and `gwfss` opening the mask twice,
once for its size and once for its colours (merging them would couple two responsibilities for no
measurable gain).

## Verification

`ruff format`, `ruff check`, `mypy` (strict) and `pytest` — 107 tests, up from 78. The new ones are
`test_labels.py`, `test_adapters_gwhd.py`, `test_adapters_gwfss.py`, plus `labels_path` and
`fetch_labels` in `test_fetch_cache.py`.

End to end, locally and without a bucket:
`biohack-datasets publish --dataset gwhd|gwfss --cache-dir .hf_cache --index-dir <tmp>` built both
indexes; `classes` came out as the pinned palette verbatim, `row_classes` as
`{1: 367, 2: 407, 3: 547}` with no `0`, `mask_kind` as `semantic`, `rles` empty for both, and
`gwhd`'s first two rows carried `Arvalis_11/France/VLB/Filling` and
`Arvalis_3/France/Gréoux/Filling - Ripening`.

## Resolved — the cache is whole, and a partial one is refused

The partial cache was the last real hazard: `parquet_files` could not tell a complete cache from a
subset, so a publish over one shard produced an index that was short and, for a shard that was not
first, misnumbered. It was worse than "fewer rows": gwhd's sorted order is **test, train-00000,
train-00001, validation**, so the smoke publish numbered `train-00000` from 0 when its true rows are
1381-3208 — every object would have landed on an address belonging to another split, and the
write-once rule would have made the second, correct publish stop on a different-bytes collision.

Two things closed it:

- `missing_shards` parses `-NNNNN-of-MMMMM` from the file names and `publish_dataset` raises
  `IncompleteCache` naming the missing file, **for a dry run too**: the plan a dry run prints is only
  honest if the cache it would read is whole. A name with no shard number is skipped, because nothing
  offline can say how many of those there should be.
- The cache is fetched whole. `.agents/rules/datasets.md` now says a publishable cache is the whole
  selection, and that ~1.9 GB locally is the price of testing the whole path rather than a slice.

The full pre-flight, over the complete selection and with no bucket, built both indexes:

| | gwhd | gwfss |
|---|---|---|
| rows | 6512 | 1096 |
| objects | 275 371 | 0 (the mask is the annotation) |
| splits (first row) | test 0, train 1381, validation 5036 | labelled 0 |
| extensions | jpg, all 6512 | image png, mask png |
| dimensions | 1024² everywhere | 987 rows 512², 109 rows 1024² |
| classes | none | the pinned palette verbatim |
| `row_classes` | n/a | head 739, stem 847, leaf 1080 (no background) |

Two things this settles. First, **the plan's open item is closed**: 109 of the 1096 masks are 1024²,
exactly as recorded, and every one of them matched its image's size — the adapter would have stopped
and named the row otherwise, and it did not. Second, the plan's *illustrative* facet counts
(`head 680 / stem 823 / leaf 1096`) do not match the measured `739 / 847 / 1080`. The measurement is
from the bytes and the example was a shape rather than a measurement; the ✅ plan is frozen, so the
numbers live here.

## Also done

`sources.yaml`, `docs/data-layout.md`, `docs/examples/label_masks.py`, `.agents/rules/datasets.md`
and `README.md` were updated for the URL form, the background rule, the header dimensions, and the
fetch of the vocabulary. The finished plan `2026101518_✅dataset-publisher.md` and the session record
`2026101512_dataset-publisher.md` were **not** rewritten: they are historical records of the format
that was in force then, and the ✅ plan is excluded from updates by the working agreement.
