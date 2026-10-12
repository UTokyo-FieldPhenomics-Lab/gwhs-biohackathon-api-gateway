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
| `gwfss` | `GlobalWheat/GWFSS_v1.0` | `labelled` | 1,096 | segmentation mask |
| `gwhs-temp` | `GWHS/sample_dataset` | `train` | sample only | objects, tags |
| `gwhs` | pending full annotation | — | — | objects, tags |

`gwhs-temp` exists for development and testing only. It is not referenced by public material, and
`gwhs` arrives as a separate dataset when the full annotation is released.

Upstream revisions and licenses are recorded in `datasets/sources.yaml`, which is the provenance
record. Distributing a dataset publicly requires its license to be cleared there first.

## S3 object layout

```
{dataset}/images/{row}.{ext}      one image per row
{dataset}/masks/{row}.{ext}       one mask per row, where the upstream carries one
```

- `{row}` is the absolute row number of the pinned upstream revision, zero-padded in the object
  name but **not** in an address.
- `{ext}` follows the encoding the upstream stored, which the publisher copies byte for byte. The
  publisher never re-encodes, so an extension is uniform per kind and fixed at release. If an
  upstream stored a mask in a lossy encoding, that is a source-data question and the publisher
  does not repair it — the same stance it takes on duplicate rows.
- Objects are written once. A published path is never overwritten; changed content means a new
  path. There is no version prefix.
- The layout is identical in dev and prod; only the bucket and distribution differ.

Annotation bytes are not objects. Boxes and tags live in the metadata index; masks are their own
objects because they are images.

## Addressing

An address is `(dataset, row)`. The row number alone is ambiguous, because every dataset numbers
its rows from zero.

- `row` is the row number in the pinned upstream revision, not a position in any view.
- A position inside a filtered result — what a client gets after applying `?country=` and friends
  — is a **cursor**. It is valid only next to the filter that produced it and is never persisted
  or published.
- An upstream's own id, where it has one (`gwhs-temp` carries `image_id`), is a field in the index
  and never a second way to address the same row.

## Metadata index

One JSONL artifact per dataset, produced by the publisher, loaded by the gateway at startup. It is
a build artifact on the host's data volume, not a published object. Field names are the upstream
column names kept verbatim — including the long ones — so that no vocabulary of ours has to be kept
in step with theirs. The earlier `site=` / `stage=` query naming is therefore abandoned.

| Field | Applies to | Notes |
|---|---|---|
| `row` | all | absolute row number |
| `split` | all | structural: the upstream split name, not a column |
| `width`, `height` | all | pixels |
| `image_id` | where the upstream has one | carried, never used for addressing |
| `domain`, `location`, `country`, `development_stage` | where the upstream has them | kept verbatim |
| `tags` | where the upstream has them | list; a filter value means *contains* |
| `objects` | where the upstream has them | boxes as `[x, y, w, h]`, plus category names |

`tags_onehot` is the same information as `tags` in a worse shape and is not carried.

Facets — the keys and their available values and counts — are a deterministic projection of this
index, computed once at startup. They are not a second store.

## Manifest

One per dataset, next to the index.

- `dataset`, `revision` (upstream), `rules` (publisher version), `license`
- `counts`: rows per split, and object counts per kind
- `generated_at`, `release_date`

## Filters

The filterable keys are the index fields, which means they differ per dataset: `gwfss` offers only
`split`. A query for a key a dataset does not have returns an empty result, not an error.
