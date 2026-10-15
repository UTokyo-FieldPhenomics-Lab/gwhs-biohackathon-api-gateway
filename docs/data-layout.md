# Data Layout

Handwritten contract for the dataset plane: where image objects live, how an item is addressed,
and what the metadata index contains. Together with the Pydantic models in `gateway/`, this is one
of only two handwritten contract sources in the repository. Everything schema-shaped is a
projection of those two: request and response bodies belong to the Pydantic models, and only the
object layout and the metadata index belong here.

## Datasets

One dataset id per upstream and one top-level prefix per dataset id. A preliminary or partial
release takes its own id, so that a later full release never renumbers an address already in use.

| Dataset id | Upstream | Splits | Rows | Annotation |
|---|---|---|---|---|
| `gwhd` | `Etienne-David/GlobalWheatHeadDataset2021` | `train`, `validation`, `test` | 6,512 | bounding boxes |
| `gwfss` | `GlobalWheat/GWFSS_v1.0` | `labelled` | 1,096 | semantic mask image |
| `gwhs-temp` | `GWHS/sample_dataset` | `train` | 10 (sample) | objects: boxes, RLE geometry, tags, attributes |
| `gwhs` | pending full annotation | — | — | objects: boxes, RLE geometry, tags, attributes |

`gwhs-temp` exists for development and testing only. It is the rehearsal target: it exercises the
whole publish path for the price of ten rows. It is not referenced by public material, and `gwhs`
arrives as a separate dataset when the full annotation is released.

Upstream revisions, adapters, class vocabularies, and licenses are recorded in
`datasets/sources.yaml`, which is the provenance record. Distributing a dataset publicly requires
its license to be cleared there first.

## S3 object layout

```
{dataset}/images/{row:08d}.{ext}      one image per row
{dataset}/masks/{row:08d}.{ext}       one mask per row, where the upstream carries one
```

- `{row}` is the absolute row number of the pinned upstream revision, zero-padded to **eight
  digits** in the object name but **not** in an address. It is the zero-based cumulative index over
  the dataset's parquet files in sorted path order, which is what makes it deterministic.
- `{ext}` is a **per-row** fact, sniffed from the object's leading bytes and lower-cased. It is
  not uniform per kind and not fixed at release: one `gwhs-temp` sample mixes `png`, `JPG`, and
  `jpg`, and the `gwfss` masks are 512² *and* 1024². A mask's pixel dimensions are the file's own,
  not necessarily the image's. The publisher never re-encodes, so if an upstream stored a mask in a
  lossy encoding that is a source-data question and the publisher does not repair it — the same
  stance it takes on duplicate rows.
- Objects are written once. A published path is never overwritten; changed content means a new
  path. There is no version prefix. A correction to an object's headers is not a content change, and
  repairing an object already being served is an explicit, deliberate action — the break-glass in the
  deploy rules — never a routine publish.
- The layout is identical in every environment; only the bucket and distribution differ.

Annotation bytes are not automatically objects, nor is the reverse. Object geometry the upstream
published as COCO RLE lives as a value in the metadata index and is returned on demand; a mask the
upstream shipped as an image stays an object and is byte-copied like one.

## Addressing

An address is `(dataset, row)`. The row number alone is ambiguous, because every dataset numbers
its rows from zero.

- `row` is the absolute row number of the pinned upstream revision, not a position in any view.
- A position inside a filtered result — what a client gets after applying `?country=` and friends
  — is a **cursor**. It is valid only next to the filter that produced it and is never persisted
  or published.
- An upstream's own id, where it has one (`gwhs-temp` carries `image_id`), is a field in the index
  and never a second way to address the same row. It is carried, not claimed to be unique.

## Metadata index

One SQLite file per dataset, `~/.config/biohack-gateway/index/<dataset>.db`, produced by the
publisher and loaded by the gateway at startup. It is a build artifact on the gateway host, not a
published object. There are **eight tables**, one row per image in `rows`:

```sql
CREATE TABLE dataset (            -- exactly one row: the provenance a manifest used to hold
  id           TEXT PRIMARY KEY,
  revision     TEXT NOT NULL,     -- pinned upstream sha
  rules        TEXT NOT NULL,     -- installed biohack-datasets version
  license      TEXT,              -- NULL until cleared
  mask_kind    TEXT,              -- NULL | 'semantic' | 'instance'
  generated_at TEXT NOT NULL
);
CREATE TABLE classes (            -- the palette vocabulary of a mask annotation, where one exists
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL   -- colour as '#rrggbb'
);
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
CREATE TABLE row_classes (        -- derived at publish time: palette classes this row's mask has
  row INTEGER NOT NULL, class_id INTEGER NOT NULL, PRIMARY KEY (row, class_id)
) WITHOUT ROWID;
CREATE TABLE tags (
  row INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (row, tag)
) WITHOUT ROWID;
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

- **There is no manifest.** Its fields are the single `dataset` row, and the API is the read path.
  Counts — rows per split, objects per category, class presence — are **queries, not stored facts**:
  a stored count is a second copy that can drift.
- **There is no `label` column.** The upstream parquet has no such field; `gwhs` objects carry
  `category`/`bbox`/`rle`/`area`/`attributes` and nothing else. A raster label, if one is ever
  rendered, is `idx + 1`.
- **There are no secondary indexes.** `(row, idx)` is the only lookup the API makes, and a facet is
  a `GROUP BY` over at most ~100 k object rows. The trigger to add one is a facet query over
  ~50 ms on the host.
- **The file is built beside its target and moved into place with `os.replace`**, so a reader never
  sees a half-written index. `journal_mode=DELETE`; WAL would be a second file.
- **Deterministic content**: fixed insert order, fixed float formatting, `VACUUM` at the end. Byte
  identity across machines is not claimed, because SQLite's page layout is version-dependent; the
  check is a canonical ordered read-back.
- **The class vocabulary is provenance.** Where a dataset has one, the palette and names are pinned
  in `sources.yaml` as `<repo>@<sha>#<path>` (for `gwfss`,
  `GlobalWheat/GWFSS_Segformer_b0@485865a…`'s `class.json`) and read into `classes` and
  `row_classes`. It is not a constant in code, and it is not the colours of a paper figure: the
  stored palette is the only authority.
- **The RLE is verbatim.** `rles.counts` is the upstream's compressed string, never re-encoded, and
  `rles.height`/`rles.width` are its `[h, w]` `size` copied as published. The client decodes it
  with a COCO RLE decoder; the API never decodes.
- **Fields dropped from the upstream parquet**, because they are the same information in a worse
  shape or empty: `tags_onehot`, `attributes_onehot`, and `polygons` (empty in 100 % of the
  `gwhs` sample). `objects.area` is the upstream's own value, kept because it independently
  verifies the RLE; the publisher never computes an area.
- **`gwhd` objects have no upstream vocabulary.** Its `objects.categories` are all `0` and the
  upstream declares no `ClassLabel` metadata, so the index stores the upstream integer as text and
  invents no class name.

## Filters

The filterable keys are the index fields, which means they differ per dataset: `gwfss` offers
`split` and `class`; `gwhs` offers `split`, `tags`, `category`, and `attribute`; `gwhd` offers
`split`, `domain`, `country`, `location`, and `development_stage`. A query for a key a dataset
does not have returns an empty result, not an error.

A **list** response carries identification, addresses, and a `statistics` block — counts for a
detection or instance dataset, the set of classes present for a semantic one — and never the
per-image annotation. The annotation itself is one row at a time from the detail endpoint, which
returns the object geometry where the dataset has it.
