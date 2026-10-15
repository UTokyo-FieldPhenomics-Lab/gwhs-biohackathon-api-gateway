# Dataset publisher: `gwhs-temp` first, with the `gwhs` adapter

Status: **implemented locally, host acceptance pending** (2026-10-15) — see
[`../references/2026101512_dataset-publisher.md`](../references/2026101512_dataset-publisher.md)
Owner files: `datasets/src/biohack_datasets/*`, `datasets/sources.yaml`, `datasets/tests/*`,
`docs/data-layout.md`, `docs/examples/*`, `deploy/README.md`, `deploy/systemd/biohack-gateway.service`,
`.agents/rules/{datasets,project,deploy,gateway}.md`, `README.md`.

## Goal and acceptance

Run the whole publish path on the **smallest real dataset** (`gwhs-temp`, 10 rows) so the pipeline is
proven end to end before any 6.5k-row dataset touches S3, using the adapter `gwhs` will reuse.

One invocation does plan → build → validate → upload → verify and exits non-zero on any failure.

Accepted when, **on the host**:

1. `uv run biohack-datasets publish --dataset gwhs-temp --dry-run` prints the plan, the bucket it
   would write to, and writes nothing.
2. `uv run biohack-datasets publish --dataset gwhs-temp --bucket s3://<bucket>` uploads 10 image
   objects and writes `~/.config/biohack-gateway/index/gwhs-temp.db`.
3. `curl -sI https://data.global-wheat.com/gwhs-temp/images/00000000.jpg` answers
   `content-type: image/jpeg` and `cache-control: public, max-age=31536000, immutable`; the second call
   answers `x-cache: Hit from cloudfront`.
4. The same object through the S3 endpoint answers `403` (private bucket).
5. A byte comparison of the object fetched from `data.*` against the bytes in the cached parquet is
   identical (`cmp`), for one JPEG row and for the 4 MB PNG row.
6. A second run of the same command reports every object skipped and uploads nothing.
7. The index reads back: 10 rows, 199 objects, 199 RLE entries, tags and attributes resolved to names,
   and the run's closing summary prints dataset / revision / rules / license.

## Measured facts this plan rests on

Every number was measured against the pinned revisions — not assumed.

| Fact | Value |
|---|---|
| `gwhs-temp` | 10 rows, **199 objects** (~20/image, one image has 25), 11.27 MB of image bytes |
| `gwhs-temp` extensions | **mixed in one dataset**: `png`, `JPG`, `jpg`; largest object 4,059,916 B (RGBA PNG) |
| `gwhs` geometry | objects carry `bbox`, `rle{size,counts}`, `area`, `attributes` — and `polygons` is **empty in 100 %** |
| `gwhs` RLE | 66,572 B for 199 objects (6.7 KB/row); decoding all 199 reproduces the upstream `area` **and** `bbox` exactly |
| `gwhs` label axes | `category` (6) **and** `attributes` (5) — two orthogonal axes on one object |
| `gwhs` pixel overlap | **0/10 rows**: object masks are disjoint; occlusion is an attribute, not overlapping pixels |
| `gwfss` | 1,096 rows, schema is `image` + `mask` only — **no objects, no tags, no dimension columns** |
| `gwfss` palette | globally constant: 4 RGB colours, per-image subsets, **no stray colour across all 1,096 masks** |
| `gwfss` mask size | **not uniform**: 987 × 512², **109 × 1024²** |
| `gwfss` classes | published upstream in `GWFSS_Segformer_b0/class.json`: `0 background (0,0,0)`, `1 head (50,255,132)`, `2 stem (50,132,255)`, `3 leaf (214,255,50)` |
| `gwhd` | `image`, `domain`, `country`, `location`, `development_stage`, `objects{boxes, categories}`; ~47 boxes/image; `categories` all `0`, **no vocabulary** upstream |
| ClassLabel names | live in the **file-level** `huggingface` schema metadata, not per field |
| Transcoding `gwfss` to one channel | saves 37 % (13,762 → 8,702 B/mask, ~5.5 MB total) |
| A JSON mask carrier | `[[0,1,…]]` = 2.1 MB per 1024² mask vs PNG 5–15 KB vs RLE ≈ 1.4 KB |
| A run-length format of our own | the proposed `[[x,y,len],…]` is **5.6×** the COCO RLE string; `[[x,y,bool,len],…]` **16.2×** |
| `datasets-server` assets | **re-encode images to JPEG**; never usable as evidence about upstream bytes |
| The paper's figure colours | green/purple/orange — **not** the stored palette; only `class.json` is authoritative |

## Implementation

### 1. Provenance — `datasets/src/biohack_datasets/sources.py`

`Source` gains `adapter: str | None`, `license: str | None` and `labels: str | None`,
all already present in `sources.yaml` and currently ignored. `adapter` names a schema, not a dataset:
`gwhs-temp` and `gwhs` share `gwhs`. A dataset with `upstream: null` is reported and skipped, as
`fetch` already does. An adapter that is not implemented is a **hard error naming it**, never a silent
skip. `labels` is a pinned `<repo>@<sha>#<path>` reference to an upstream class vocabulary (see §5).

### 2. Adapter — `adapters/gwhs.py`

Reads the upstream parquet and yields one neutral row per absolute row number.

- **Vocabulary comes from the file-level `huggingface` schema metadata** — that is where `ClassLabel`
  names live. `tags`, `objects[].category` and `objects[].attributes` are integer indexes into it, so
  no vocabulary of ours is created.
- **`image_ext` is sniffed from the first bytes** (JPEG `FF D8 FF`, PNG `89 50 4E 47`), lower-cased.
  An unknown magic stops the run and names the row. No Pillow call is needed.
- `width`/`height` are copied from the upstream columns, never re-derived — the annotation is aligned
  to them.
- `split` comes from the parquet file name prefix; `row` is cumulative over the dataset's parquet files
  in sorted path order. That defines "absolute row" deterministically.
- Bundle is a byte copy out of `image.bytes`. Nothing is decoded and nothing is re-encoded.
- No new dependency: `pyarrow`, `Pillow`, `boto3` already cover this. **No `numpy`. No `pycocotools`** —
  the RLE is a string copy.

### 3. Parquet field → index mapping, and what is dropped

**`gwhs` / `gwhs-temp`** — `data/train-00000-of-00001.parquet`

| Parquet field | Type | Destination | Note |
|---|---|---|---|
| `image` | `struct<bytes, path>` | object body + `rows.image_ext` | byte copy; `path` only used in logs |
| `image_id` | `string` | `rows.image_id` | carried, never an address |
| `width`, `height` | `int32` | `rows.width`, `rows.height` | verbatim |
| `tags` | `list<int64>` (ClassLabel) | `tags` table, names resolved | |
| `tags_onehot` | `fixed_size_list<int8>[18]` | **dropped** | same information in a worse shape |
| `objects[].category` | `int64` (ClassLabel) | `objects.category` (name) | |
| `objects[].bbox` | `fixed_size_list<float>[4]` | `objects.x/y/w/h` | |
| `objects[].rle.size` | `fixed_size_list<int32>[2]` | `rles.height`, `rles.width` | `[h, w]`, verbatim |
| `objects[].rle.counts` | `string` | `rles.counts` | **verbatim**, never re-encoded |
| `objects[].polygons` | `list<list<float>>` | **dropped** | empty in 100 % of the sample |
| `objects[].area` | `float` | `objects.area` | kept: it independently verifies the RLE |
| `objects[].attributes` | `list<int64>` (ClassLabel) | `object_attributes.attribute` | |
| `objects[].attributes_onehot` | `fixed_size_list<int8>[5]` | **dropped** | same information in a worse shape |
| *(file name)* | | `rows.split` | `train-…` → `train` |

**`gwhd`** — `data/{train,validation,test}-*.parquet` (4 files)

| Parquet field | Destination | Note |
|---|---|---|
| `image` | object body + `rows.image_ext` | JPEG |
| `domain`, `country`, `location`, `development_stage` | `rows.*` | verbatim filter keys |
| `objects.boxes` | `objects.x/y/w/h` | `list<list<int64>>`, parallel to `categories` |
| `objects.categories` | `objects.category` | all `0`; **no vocabulary upstream**, so the value is the upstream integer as text |
| *(no dimension columns)* | `rows.width/height` | read from the image header |
| `objects.area`, `objects.rle`, `tags`, mask | **absent** | `area` and `rle` stay NULL/empty |

**`gwfss`** — `data/labelled-*.parquet` (2 files)

| Parquet field | Destination | Note |
|---|---|---|
| `image` | object body + `rows.image_ext` | |
| `mask` | object body + `rows.mask_ext` | byte copy → `masks/{row}.{ext}` |
| *(no dimension columns)* | `rows.width/height` | read from both headers; **assert image dims == mask dims**, stop with the row named otherwise |
| *(the mask's colours)* | `classes` table + `row_classes` | derived at publish time, not upstream columns |
| objects, tags | **absent** | the `objects`/`tags` tables are empty for this dataset |

**Object-to-image correspondence.** `image_id` is an image-level field, so it lives on `rows`, where the
upstream publishes it (`gwhs` does; `gwhd` and `gwfss` publish none). Every object row carries `row`,
which **is** that image's absolute row and the primary key of `rows`, so one image maps to its many
objects by `WHERE row = ?` and one object maps back to its image by construction. No `image_id` column
is added to `objects`: it would duplicate a derivable fact, and `image_id` is *carried*, never used for
addressing — it is not claimed to be unique, and the address stays `(dataset, row)`.

### 4. Index schema — one `<dataset>.db`, eight tables

```sql
-- Dataset level
CREATE TABLE dataset (            -- exactly one row; the free-text provenance a manifest used to hold
  id           TEXT PRIMARY KEY,
  revision     TEXT NOT NULL,     -- pinned upstream sha
  rules        TEXT NOT NULL,     -- installed biohack-datasets version
  license      TEXT,              -- NULL until cleared
  mask_kind    TEXT,              -- NULL | 'semantic' | 'instance'
  generated_at TEXT NOT NULL
);
CREATE TABLE classes (            -- the palette vocabulary of a mask annotation (gwfss today)
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL   -- colour as '#rrggbb'
);

-- Per row
CREATE TABLE rows (
  row       INTEGER PRIMARY KEY,  -- absolute row, 0-based, cumulative over sorted parquet paths
  split     TEXT    NOT NULL,     -- from the parquet file name prefix
  image_ext TEXT    NOT NULL,     -- 'jpg' | 'png', sniffed from the bytes, lower-cased
  mask_ext  TEXT,                 -- 'jpg' | 'png'; NULL when the dataset has no mask object
  width     INTEGER NOT NULL,     -- image pixels
  height    INTEGER NOT NULL,
  image_id  TEXT,                 -- upstream id verbatim; never an address
  domain TEXT, country TEXT, location TEXT, development_stage TEXT
);
CREATE TABLE row_classes (        -- derived at publish time: which palette classes this row's mask has
  row INTEGER NOT NULL, class_id INTEGER NOT NULL, PRIMARY KEY (row, class_id)
) WITHOUT ROWID;
CREATE TABLE tags (
  row INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (row, tag)
) WITHOUT ROWID;

-- Per object
CREATE TABLE objects (
  row INTEGER NOT NULL, idx INTEGER NOT NULL,
  category TEXT NOT NULL,         -- resolved name, or the upstream integer as text
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  area REAL,                      -- NULL where the upstream publishes none; never computed
  PRIMARY KEY (row, idx)
) WITHOUT ROWID;
CREATE TABLE object_attributes (
  row INTEGER NOT NULL, idx INTEGER NOT NULL, attribute TEXT NOT NULL,
  PRIMARY KEY (row, idx, attribute)
) WITHOUT ROWID;
CREATE TABLE rles (               -- cold: no list query ever touches it
  row INTEGER NOT NULL, idx INTEGER NOT NULL,
  height INTEGER NOT NULL, width INTEGER NOT NULL, counts TEXT NOT NULL,
  PRIMARY KEY (row, idx)
) WITHOUT ROWID;
```

- **No `manifest.json`.** Its fields are the single `dataset` row, and the API is the read path. Counts
  (`rows per split`, `objects per category`, class presence) are **queries, not stored facts** — a
  stored count is a second copy that can drift.
- **No `label` column.** The upstream parquet has no such field; gwhs objects carry
  `category`/`bbox`/`rle`/`area`/`attributes` and nothing else. A raster label, if one is ever rendered,
  is `idx + 1`.
- **No secondary indexes.** `(row, idx)` is the only lookup the API makes, and a facet is a `GROUP BY`
  over at most ~100 k object rows. Trigger to add one: a facet query over ~50 ms on the host.
- The `.db` is built beside its target and moved into place with `os.replace`, so a reader never sees a
  half-written index. `journal_mode=DELETE` — WAL would be a second file.
- Deterministic content: fixed insert order, fixed float formatting, `VACUUM` at the end. Byte identity
  across machines is **not** claimed (SQLite page layout is version-dependent); the check is a canonical
  ordered read-back.
- **Where it lives:** `~/.config/biohack-gateway/index/<dataset>.db`, resolved from `$HOME` the way
  `tokens.py` resolves `tokens.json`, with `--index-dir` for a laptop run. The index is host state, not
  checkout content: it survives `git clean -fdx`, and the gateway owns where it reads from.

### 5. The `gwfss` class vocabulary, as provenance

The palette and the class names are **published upstream** in the model repository next to the dataset:

```
GlobalWheat/GWFSS_Segformer_b0@485865aa6c67ec78a2400e3dfd888d8847186c49#class.json
  [{"id":0,"name":"background","color":[0,0,0]},
   {"id":1,"name":"head",      "color":[50,255,132]},
   {"id":2,"name":"stem",      "color":[50,132,255]},
   {"id":3,"name":"leaf",      "color":[214,255,50]}]
```

It matches the three foreground colours measured in the parquet exactly, and the pixel shares
corroborate it (leaf 60 %, head in 62/100 rows, stem in 75/100). `sources.yaml` records it:

```yaml
  gwfss:
    adapter: gwfss
    upstream: GlobalWheat/GWFSS_v1.0@d32eb4a70811fa4107000e0b8821e892df710830
    labels: GlobalWheat/GWFSS_Segformer_b0@485865aa6c67ec78a2400e3dfd888d8847186c49#class.json
    license: cc-by-4.0
```

The mapping is **provenance read from `sources.yaml`**, not a constant in code, and it is what fills the
`classes` table and names the rows in `row_classes`. The paper's figure colours (green/purple/orange)
are a rendering and are **not** the stored palette — do not derive anything from them.

### 6. Validation, before anything is written

Rows contiguous from 0; every `NOT NULL` present; `image_ext` and `mask_ext` in `{jpg, png}`; every
object's `idx` dense from 0; every `rle.counts` non-empty and its `height`/`width` equal to the row's;
every tag, category, attribute and class name non-empty; the `dataset.revision` equal to the value
pinned in `sources.yaml`. A failure stops the run with the offending row named.

### 7. S3 layer — `s3.py`

A narrow protocol (`head`, `put`) with a boto3 implementation and an in-memory fake for tests.

- One `HeadObject` first: absent → `PUT`; present → compare **byte size, `ETag`, `Content-Type`,
  `Cache-Control`**. All four equal → skip. Equal bytes with a different header → re-`PUT` the same bytes
  (not an overwrite: the content did not change). Different bytes → **stop and name the key**; the
  operator chooses between a new dataset id and a deliberate wipe of a prefix still under development.
- Every object is one single-part `PUT`, which keeps `ETag` equal to the content MD5 and makes the
  comparison cheap. `Content-Type` comes from the sniffed extension;
  `Cache-Control: public, max-age=31536000, immutable`.
- Reads are serial, uploads concurrent (default 16). Peak memory is one row group.
- Verify after upload: one `HEAD` per object compares `ContentLength` against the byte length we sent,
  `ETag` against the `ETag` the `PUT` answered with, and both headers. No local hashing.

## Command line, in full

One installed command, two subcommands — the shape `biohack-gateway tokens list|add|remove` already
uses. `biohack-datasets-fetch` disappears as a separate console script.

```
biohack-datasets fetch   [--dataset ID ...] [--cache-dir DIR] [--dry-run] [--concurrency N]
biohack-datasets publish [--dataset ID ...] [--bucket s3://BUCKET] [--index-dir DIR] [--dry-run]
                         [--concurrency N]
```

| Flag | Default | Meaning |
|---|---|---|
| `--dataset ID [ID ...]` | every dataset with a pinned upstream | which datasets to handle; an unimplemented adapter exits 1 with its name |
| `--cache-dir DIR` | `datasets/.hf_cache` | upstream parquet cache |
| `--index-dir DIR` | `~/.config/biohack-gateway/index` | where `<dataset>.db` is written |
| `--bucket s3://BUCKET` | `$BIOHACK_BUCKET`, else none | omitted = build locally, upload nothing |
| `--dry-run` | off | print the plan (including the bucket it would write to), write nothing, upload nothing |
| `--concurrency N` | 16 | parallel uploads (reads stay serial) |

`publish` never downloads: if the cache for a dataset is missing it exits non-zero and names
`biohack-datasets fetch`.

**Configuration comes from the environment only.** The code reads `BIOHACK_BUCKET`, `AWS_REGION` and
`BIOHACK_ACCESS_KEYS_CSV`; the host keeps them in a file the operator sources, outside the checkout:

```bash
set -a; . ~/.config/biohack-gateway/publish/env; set +a
```

`publish/env` is 0600 and holds those three names; the key itself is the console's access-key CSV,
`publish/awsAccessKeys.csv`, placed beside it. The publisher reads that CSV -- an SDK cannot, since a
shared-credentials file is INI -- and hands the pair straight to its boto3 session. **The secret is not
in the environment**, because the environment is the leaky surface (`/proc/<pid>/environ`, logs, crash
dumps, child processes); the CSV is. Two things together keep the key out of the gateway's reach: it is not in the gateway's `env` — the file its unit reads through `EnvironmentFile=` — and the
unit excludes the publisher's directory outright:

```ini
InaccessiblePaths=%h/.config/biohack-gateway/publish
```

The service and the publisher run as the same user, so file modes alone cannot separate them. Excluding
the *directory* rather than the individual files is what keeps a file added there later from being
readable by accident.

The same credential kept at the default `~/.aws/credentials` would be readable by the service — same
user, 0600 — and safe by convention only, which is why it is not kept there.

```bash
# workstation: adapter development against real bytes, nothing published
cd datasets && uv sync
uv run biohack-datasets fetch --dataset gwhs-temp --dry-run
uv run biohack-datasets fetch --dataset gwhs-temp              # 11.4 MB into datasets/.hf_cache/

# host, in the bucket's region, with the cache present
export CHECKOUT=/opt/gwhs-biohackathon-api-gateway
set -a; . ~/.config/biohack-gateway/publish/env; set +a
cd "$CHECKOUT/datasets"
uv run biohack-datasets publish --dataset gwhs-temp --dry-run
uv run biohack-datasets publish --dataset gwhs-temp --index-dir ./build    # inspect locally
uv run biohack-datasets publish --dataset gwhs-temp                        # into the host index dir
uv run biohack-datasets publish --dataset gwhs-temp --bucket s3://<bucket> --concurrency 16
```

Object keys are `{dataset}/images/{row:08d}.{image_ext}` and, where a mask exists,
`{dataset}/masks/{row:08d}.{mask_ext}`.

## The JSON the index is built to serve

This is the dataset surface's contract, shown here because the index must contain everything these
responses need. It is **implemented in the follow-up item**, not this one. Two existing rules shape it:
every endpoint requires a key, and a position inside a filtered result is a **cursor**, never an
identifier.

### `GET /datasets` — the directory, one line per dataset

```json
{
  "datasets": [
    {"id": "gwhd", "rows": 6512, "splits": {"train": 3655, "validation": 1476, "test": 1381},
     "geometry": "boxes", "mask": null},
    {"id": "gwfss", "rows": 1096, "splits": {"labelled": 1096},
     "geometry": null, "mask": {"kind": "semantic"}},
    {"id": "gwhs", "rows": 5000, "splits": {"train": 5000},
     "geometry": "rle", "mask": null}
  ]
}
```

No filter keys and no palette here: those belong to the dataset's own description, fetched once.

### `GET /datasets/{dataset}` — the dataset's contract, fetched once per session

```json
{
  "dataset": "gwfss", "revision": "d32eb4a7…", "rules": "0.1.0", "license": "cc-by-4.0",
  "rows": 1096, "generated_at": "2026-10-15T09:00:00Z",
  "splits": {"labelled": 1096},
  "classes": [
    {"id": 0, "name": "background", "color": "#000000"},
    {"id": 1, "name": "head",       "color": "#32ff84"},
    {"id": 2, "name": "stem",       "color": "#3284ff"},
    {"id": 3, "name": "leaf",       "color": "#d6ff32"}
  ],
  "filters": {
    "split": {"labelled": 1096},
    "class": {"head": 680, "stem": 823, "leaf": 1096}
  }
}
```

`classes` is returned **here and nowhere else**: it is an immutable property of the release, so
repeating it in every response would be a duplicated constant that clients learn to read from one row
and ignore in the rest. Per-row responses name classes rather than repeating the mapping.

For `gwhs`, `classes` is absent and `filters` carries `split`, `tags`, `category` and `attribute`; for
`gwhd`, `filters` carries `split`, `domain`, `country`, `location`, `development_stage`.

### `GET /datasets/{dataset}/images?<filter>&limit=&cursor=`

Rows carry identification, addresses, and a **`statistics`** block of counts and presence sets — never
the per-image annotation itself.

```json
{
  "dataset": "gwhd", "count": 2, "next_cursor": "eyJvIjoxMDB9",
  "images": [
    {"row": 0, "split": "train", "width": 1024, "height": 1024,
     "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
     "domain": "field", "country": "Germany", "location": "…", "development_stage": "…",
     "statistics": {"objects": 47}}
  ]
}
```

```json
{
  "dataset": "gwfss", "count": 1, "next_cursor": null,
  "images": [
    {"row": 0, "split": "labelled", "width": 512, "height": 512,
     "image_url": "https://data.global-wheat.com/gwfss/images/00000000.png",
     "mask_url": "https://data.global-wheat.com/gwfss/masks/00000000.png",
     "statistics": {"classes": ["head", "leaf"]}}
  ]
}
```

```json
{
  "dataset": "gwhs-temp", "count": 1, "next_cursor": null,
  "images": [
    {"row": 0, "split": "train", "width": 592, "height": 2509,
     "image_url": "https://data.global-wheat.com/gwhs-temp/images/00000000.jpg", "mask_url": null,
     "image_id": "149_ear_001.JPG",
     "statistics": {"objects": 24, "tags": 5}}
  ]
}
```

`statistics` is the general-information block for a list: counts for detection and instance datasets,
the set of classes present for a semantic one. Tag *values* are not in a list — a client that filtered
by `tags=` knows what it asked for, and one that wants to render them reads the row. The largest page
stays 1,000 rows and there is no dataset-dump endpoint.

### `GET /datasets/{dataset}/images/{row}` — the annotation endpoint

`gwhd`:

```json
{
  "dataset": "gwhd", "row": 0, "split": "train", "width": 1024, "height": 1024,
  "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
  "domain": "field", "country": "Germany", "location": "…", "development_stage": "…",
  "objects": [{"idx": 0, "category": "0", "bbox": [936, 4, 41, 78]}]
}
```

`gwfss` — no objects; the mask is the annotation, and it is an image:

```json
{
  "dataset": "gwfss", "row": 0, "split": "labelled", "width": 512, "height": 512,
  "image_url": "https://data.global-wheat.com/gwfss/images/00000000.png",
  "mask_url": "https://data.global-wheat.com/gwfss/masks/00000000.png"
}
```

`gwhs` — geometry included, always:

```json
{
  "dataset": "gwhs-temp", "row": 0, "split": "train", "width": 592, "height": 2509,
  "image_url": "https://data.global-wheat.com/gwhs-temp/images/00000000.jpg", "mask_url": null,
  "image_id": "149_ear_001.JPG",
  "tags": ["Labo", "frontal_view", "Entirely_visible", "good_quality", "healthy"],
  "objects": [
    {"idx": 0, "category": "spikelet_-_side1", "bbox": [231.0, 923.0, 126.0, 162.0],
     "area": 12074.0, "attributes": ["Green-Spikelet"],
     "rle": {"size": [2509, 592], "counts": "Zofa05S^2;E;E;F8K4L5K4L4M3L5L2O1N2O1N3M2M3H8M3…"}},
    {"idx": 1, "category": "spikelet_-_side2", "bbox": [208.0, 939.0, 41.0, 77.0],
     "area": 1222.0, "attributes": ["Green-Spikelet"],
     "rle": {"size": [2509, 592], "counts": "man?:Q^241OQbM5Z]2`0O001O00001O00001O2N005K3M2N…"}}
  ]
}
```

There is **no `?include=rle`**: a detail response is one row, whose geometry measures 7–13 KB (199
objects over 10 rows, 334 B each) while the same row without it is 2–3 KB. Two interface paths cost more
than 10 KB of payload. Trigger to bring a parameter back: a dataset whose per-row geometry exceeds
~100 KB, which means hundreds of objects per image.

Objects are nested under the row that carries `image_id`, so the pairing is explicit without a join: an
export that wants COCO's `annotations[].image_id` copies `row` and `image_id` from the parent once and
stamps them on every nested object.

### Errors

Unchanged: one shape for the whole API, `{"error": {"code": "...", "message": "..."}}`, with
`invalid_request` / `invalid_key` / `not_found` / `quota_exceeded` / `upstream_error` / `unavailable` /
`upstream_timeout` / `internal_error` already defined. An unknown filter key is not an error — a dataset
that does not have that key returns an empty result, per `data-layout.md`.

### Queries the surface runs (proving the schema serves it)

```sql
-- list page, filtered
SELECT row, split, width, height, image_ext, mask_ext, image_id, country
FROM rows WHERE country = ? ORDER BY row LIMIT ? OFFSET ?;
-- statistics for that page
SELECT row, count(*) FROM objects WHERE row IN (…) GROUP BY row;
SELECT row, class_id FROM row_classes WHERE row IN (…);
-- detail
SELECT idx, category, x, y, w, h, area FROM objects WHERE row = ? ORDER BY idx;
SELECT height, width, counts FROM rles     WHERE row = ? ORDER BY idx;
SELECT id, name, color FROM classes ORDER BY id;
-- facets
SELECT tag, count(DISTINCT row) FROM tags    GROUP BY tag;
SELECT class_id, count(*)       FROM row_classes GROUP BY class_id;
SELECT category, count(DISTINCT row) FROM objects GROUP BY category;
```

## Tests, in the order they are written

1. `sources`: `adapter`, `license` and `labels` parse; `license: null` is allowed; a missing adapter
   errors; a `labels` reference that is not `<repo>@<sha>#<path>` errors.
2. Extension sniffing: JPEG, PNG, unknown magic.
3. `adapters/gwhs`: a synthetic parquet fixture carrying the real file-level `huggingface` metadata
   yields the right names, per-row `image_ext`, verbatim RLE, and ignores `*_onehot`.
4. Index build: all eight tables round-trip; validation refusals (a NULL width, a short RLE, a
   non-contiguous row, a revision that disagrees with `sources.yaml`); a rebuild produces the same
   canonical read-back; the file is replaced atomically.
5. `s3` against the fake: skip / re-`PUT` / stop-and-name / verify mismatch.
6. `cli`: `publish --dry-run` writes nothing and exits 0 and prints the bucket; an unimplemented adapter
   exits 1 with its name; a missing cache exits 1 and names `biohack-datasets fetch`; `--bucket` falls
   back to `BIOHACK_BUCKET`.

A `network`-marked test fetches one parquet per dataset so the adapter is written against real bytes; it
is deselected by default, like the existing fetch tests.

## Verification

```bash
cd datasets
uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
uv run pytest -m network
uv run biohack-datasets fetch --dataset gwhs-temp --dry-run
uv run biohack-datasets publish --dataset gwhs-temp --index-dir /tmp/opencode/index
sqlite3 /tmp/opencode/index/gwhs-temp.db \
  'select count(*) from rows; select count(*) from objects; select count(*) from rles;'
```

then the seven host acceptance checks at the top of this file.

## Rule and document updates

### `docs/data-layout.md` — the largest edit

| Change | Why |
|---|---|
| **Delete** "an extension is uniform per kind and fixed at release" | measured false twice: one `gwhs-temp` sample mixes `png`/`JPG`/`jpg`, and `gwfss` masks are 512² *and* 1024². `ext` becomes a per-row fact in the index |
| **Replace** the "Metadata index — one JSONL artifact" and "Manifest" sections with the eight-table schema | the artifact is one SQLite file per dataset; there is no separate manifest |
| Objects: state that per-instance `attributes` are carried, that `area` is the upstream's value where published, and that the upstream's RLE is carried **verbatim** | all three are in the data |
| Masks: `gwfss`'s mask is an upstream image object byte-copied to `masks/{row}.png`; **`gwhs` has no mask object** — its geometry is object-level RLE in the index; a mask's pixel dimensions are the file's own, not the image's | follows each dataset's upstream form |
| Add the class vocabulary: `classes` and `row_classes`, sourced from the pinned `class.json` reference in `sources.yaml`, with the note that a palette is not derivable from the paper's figures | the mapping is needed to name anything |
| Addressing: zero-padding is **8 digits**, and the absolute row number is the cumulative index over the dataset's parquet files in sorted path order | both were implied and are load-bearing |
| Note that `gwhd`'s `objects.categories` have **no upstream vocabulary** | measured: all `0`, no `ClassLabel` metadata |
| Note that a list response carries `statistics` and never the per-image annotation | keeps the two endpoints distinct |

### `.agents/rules/datasets.md`

| Change | Why |
|---|---|
| "One command, **four switches**" → one command, two subcommands (`fetch`, `publish`), with the switches named | `--release` is gone and `fetch` is folded in |
| "the largest is about 1.2 MB" → **~4.1 MB** | measured 4,059,916 B |
| "Deterministic output … so dev output is byte-comparable with prod" → "…so a republish is reproducible rather than merely repeated", plus: for a SQLite artifact the guarantee is **content** determinism (canonical read-back), not byte equality | there is no dev environment; `project.md` already carries the corrected reason |
| Add: object geometry published as upstream RLE lives in the metadata index and is **not** an object; a mask the upstream shipped as an image stays an object, byte for byte | the current rule separates only "annotation bytes" from "boxes and tags" |
| Add: the index is a SQLite file built beside its target, moved into place, and written to the host state directory | the artifact changed kind and left the checkout |
| Add: a class vocabulary is **provenance**, pinned in `sources.yaml`, never a constant in code and never read from a paper figure | the palette trap |

### `.agents/rules/gateway.md`

- **One geometry encoding across both surfaces.** The model surface returns the same object shape and
  the same COCO RLE for masks as the dataset surface (`{bbox, category?, score?, rle?}`), so a
  participant writes **one** decoder for ground truth and for model output.
- **Normalisation happens in the backend's image, never in the gateway.** Each model container carries a
  post-processing step that turns that model's native output — SAM 3's raster or compressed raster among
  them — into that shape before it reaches the gateway. The gateway keeps zero ML dependencies and does
  no encoding.
- The dataset surface's list endpoints carry a `statistics` block and never the per-image annotation.

### `.agents/rules/project.md`

- The metadata exception to static-first gains one clause: the index also carries annotation
  **geometry**, returned on demand; masks that are images stay in the image plane, and no mask or image
  bytes traverse the gateway.
- The host-state bullet gains: the index lives in the host state directory beside `env` and
  `tokens.json`; the publisher's `publish/` directory — its `env` and the console's access-key CSV — is
  the gateway's unit's `InaccessiblePaths=`, so the service cannot read the key that can write the
  bucket.
- Environment separation records that `gwhs-temp` is the rehearsal target and that `gwhs` publishes only
  after its license is cleared the way `gwhd`'s and `gwfss`'s were.

### `.agents/rules/deploy.md`

- The S3 write-grant bullet gains the credential's *form*: an IAM user scoped to the dataset prefixes,
  the console's access-key CSV at `~/.config/biohack-gateway/publish/awsAccessKeys.csv` and its
  environment in `publish/env` beside it, sourced by the operator, **never** in the gateway's `env`;
  and the unit excludes that directory with `InaccessiblePaths=`.

### `deploy/README.md`

- **New §5, "The publish credential".** The AWS-console walkthrough: create the policy scoped to one
  ARN per dataset prefix, create the IAM user `biohack-publish`, attach it, create an access key, place
  the console's `.csv` download at `~/.config/biohack-gateway/publish/awsAccessKeys.csv` (0600) and
  record the three names — `BIOHACK_BUCKET`, `AWS_REGION`, `BIOHACK_ACCESS_KEYS_CSV` — in `publish/env`
  beside it, then verify through the publisher and, for a CLI spot-check, the pair exported for one
  command. The publisher reads the CSV itself, because a shared-credentials file is INI and an SDK
  cannot; the secret never enters the environment. It states the prohibitions: never the account-root
  key, never in the gateway's `env` file, never in the repository. It stays a **section** rather than a
  second document, so `deploy/` keeps one place a human goes.
- §4 "Record what you created" gains the publish profile name and the index directory
  (`~/.config/biohack-gateway/index`).
- §1.2's dataset step gains the `fetch` and `publish` invocations.
- §1.5's list of host state files gains `publish/` and the index directory.

### `deploy/systemd/biohack-gateway.service`

- One line: `InaccessiblePaths=%h/.config/biohack-gateway/publish`, with the comment saying what it is
  for — the service and the publisher run as the same user, so this is what keeps the publisher's
  credential genuinely out of the service's reach, not merely out of its environment; excluding the
  directory is what makes a file added there later safe by default.

### `README.md`

- The Datasets section is rewritten to the two subcommands and drops "The publisher is not implemented
  yet".

### `datasets/sources.yaml`

- `gwfss` gains `labels:` (the pinned `class.json` reference).
- `gwhs-temp.license` gains a note that it is a development-only rehearsal of 10 rows.
- `gwhs.license` keeps its TODO and gains the statement that publishing is gated on it.

### New: `docs/examples/`

| File | Content |
|---|---|
| `rle_decode.py` | the 15-line pure-Python COCO RLE decoder; executed in CI; verified 199/199 against the upstream `area` **and** `bbox` |
| `rle_decode.js` | the same algorithm for JS; reviewed, not executed (this repo's CI has no node) |
| `rle_decode.kt` | the same algorithm for Kotlin/Android |
| `label_masks.py` | `label_masks(image, palette) -> dict[str, np.ndarray]`, generic over palettes, shipping `GWFSS_CLASSES` (the four `id`/`name`/`color` entries) read from the same upstream `class.json`; asserts that no colour outside the palette appears |
| prose page | how to read a mask (palette → named per-class boolean arrays) and how to decode an object's RLE, in both cases with the class names from the dataset description |

Placement rule: `.agents/skills/` never leaves the repository, so anything a participant's agent is meant
to read must be published under `docs/`. The repository-internal skill file, when written, only links
there.

## Deferred — none of these is done in this item

| Deferred | Trigger |
|---|---|
| Render `gwhs` instance masks as a per-row label-map PNG on the image plane | a participant needs masks **without** a codec (a mobile app, or an agent that will not read the snippet). The renderer is prototyped and validated (199/199 on `area` **and** `bbox`). If it fires: one channel, mode `L`, never RGB, never base64, and assert per row that `sum(area) == union(area)` |
| Transcode `gwfss` masks from RGB to a single-channel label PNG | only if the measured 37 % saving (15 MB → 9.5 MB) ever matters. The class order is no longer an obstacle — it is published — so the only reason to decline is that 5.5 MB does not justify running an encoder |
| Storing per-class **pixel counts** alongside `row_classes` presence | a participant wants area-based filtering ("images where head covers > 20 %") |
| A run-length format of our own | never: COCO RLE is 5.6× smaller than the proposed JSON form and is already the upstream's, SA-1B's and the hosted SAM-3 endpoints' format |
| Move the index off SQLite | resident index > ~256 MB, or a filtered query p95 > ~50 ms on the host |
| An `?include=`/projection parameter on any endpoint | a detail whose geometry exceeds ~100 KB per row |
| A published bulk export of the index | a client that wants everything complains about ~5,000 detail requests; `data-layout.md`'s "a client that wants everything reads the index artifact" is not yet true, since the index is host-local |
| Naming `gwhd`'s single box class | when the `gwhd` adapter is written; the upstream declares no vocabulary |
| A participant-facing skill package | when the docs site is built; the examples land first |

## Open items

- **`gwfss` image-vs-mask dimensions.** 109 of 1,096 masks are 1024², the rest 512²; the first 100 rows
  agree with their images. The `gwfss` adapter must assert equality per row and stop with the row named —
  a mismatch means the annotation does not align with the photo.
- **`gwhs` license** before public release; `gwhs-temp` is the rehearsal.
- **Where the gateway reads the index from** is now settled (`~/.config/biohack-gateway/index`); the env
  var name that points at it belongs to the dataset-surface item.
- The `gwhs` figures are from a 10-row sample; the 5k-row estimates (~33 MB of RLE, ~80 MB of mask PNGs
  if the render branch ever fires) are extrapolations and are labelled as such.
- **A lesson to record in `.agents/references/` when this lands:** `datasets-server`'s asset URLs
  re-encode images, and a paper's figure colours are not the stored palette. Both cost a wrong conclusion
  in this session and both are avoidable by reading the parquet and the upstream `class.json`.
