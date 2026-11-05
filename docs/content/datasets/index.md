# Datasets

An image is addressed as `(dataset, row)`. `row` is the image's position in the dataset's pinned
upstream revision — the API's name for which image, not a stable id: it repeats across datasets, and a
new revision can renumber it.

Two datasets are published in full: `gwhd` and `gwfss` answer every image. `gwhs` is not served until
its full annotation is released; `gwhs-temp` is the ten-image development sample of it, with the same
schema.

## At a glance

| id | task | one image carries | images | licence | status |
|---|---|---|---|---|---|
| [`gwhd`](gwhd.md) | head detection | one box per head | 6,512 | CC-BY-4.0 | published |
| [`gwfss`](gwfss.md) | organ segmentation | one palette mask | 1,096 | CC-BY-4.0 | published |
| [`gwhs`](gwhs.md) | spikelet segmentation | objects (box, RLE, attributes) | pending | CC-BY-4.0 | not served |
| [`gwhs-temp`](gwhs.md) | `gwhs` sample | the same schema, ten images | 10 | none declared | development only |

`gwhs` appears here because its contract is fixed; it is not in `GET /datasets` until the annotation
is released, so a call for one of its images answers `404`. `gwhs-temp` is the callable stand-in: same
schema, ten images, and not a released dataset.

## Which one

- **Boxes around heads** → `gwhd`. Counting, density, detection — the classic task.
- **Organ pixels** → `gwfss`. One mask image, one colour per class; split it and every pixel has a
  label.
- **One object per spikelet** → `gwhs` once its annotation is released. A box *and* a mask *and*
  attributes per spike, so counts and sizes per ear become possible.
- **The same schema today** → `gwhs-temp`. Ten images for development; its card declares no licence, so
  do not treat it as a released dataset.

## What one image holds

The three share a shape, which is why one client handles all of them. A **list** page carries one
summary per image and never the annotation:

```json
{ "dataset": "gwhd", "count": 1, "next_cursor": "…",
  "images": [
    { "row": 0, "split": "test", "width": 1024, "height": 1024,
      "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
      "statistics": { "objects": 144 } }
  ] }
```

A **detail** response adds the image's annotation and its provenance:

```json
{ "dataset": "gwhd", "row": 0, "split": "test", "width": 1024, "height": 1024,
  "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
  "country": "Australia", "development_stage": "Filling - Ripening",
  "objects": [ { "idx": 0, "category": "0", "bbox": [227.0, 3.0, 58.0, 47.0] } ] }
```

- `image_url` and `mask_url` are absolute `data.*` addresses with no key. They are the only way to get
  bytes; the gateway does not carry dataset images.
- `width` and `height` are the image's own pixels, which is what `bbox` is measured in.
- `objects` appears in a detail response; `statistics` summarises an image in a list, so you can choose
  images without fetching each one.
- An image also carries whatever the dataset annotates it with — location, growth stage, tags — and those
  are exactly the filter keys the dataset publishes.

## Filters

`GET /datasets/<id>` publishes a `filters` block: every key its images can be narrowed by, and the
values that actually occur — no guessing whether `Australia` is spelled `Australia`. Send them as query
parameters (`?split=train`), repeat a key to narrow further (`?split=train&split=val` means both), and
walk the pages with the `next_cursor` a list response reports.

Each dataset page below shows one real response and the fields that are easy to misread:
[`gwhd`](gwhd.md), [`gwfss`](gwfss.md), [`gwhs`](gwhs.md).
