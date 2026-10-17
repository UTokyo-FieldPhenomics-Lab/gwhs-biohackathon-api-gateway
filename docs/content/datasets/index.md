# Datasets

One row is one image and its annotation, addressed as `(dataset, row)`. Row numbers are positions in
the dataset's pinned upstream revision, so a row number on its own means nothing — they repeat across
datasets and will be renumbered by no one.

Three datasets ship for the event. The API answers the **first ten rows** of each published one today,
which is enough to build against; the full sets are published before the hackathon, and the same calls
then return every row. `gwhs-temp` is the development sample of the third: same schema, a handful of
rows, safe to call all day.

| id | dataset | one row carries | licence |
|---|---|---|---|
| `gwhd` | Global Wheat Head Detection | an image and a bounding box per wheat head | CC-BY-4.0 |
| `gwfss` | Global Wheat Full Semantic Organ Segmentation | an image and a mask, one colour per organ | CC-BY-4.0 |
| `gwhs` | Global Wheat Head Segmentation | an image and one object per spikelet: box, mask, attributes | CC-BY-4.0 |

## Which one

- **Boxes around heads** → `gwhd`. Counting, density, detection — the classic task.
- **Organ pixels** → `gwfss`. One mask image, one colour per class; split it and every pixel has a
  label.
- **One object per spikelet** → `gwhs`. A box *and* a mask *and* attributes per spike, so counts and
  sizes per ear become possible.

## The envelope

The three share a shape, which is why one client handles all of them:

```json
{ "dataset": "gwhd", "row": 0, "split": "test", "width": 1024, "height": 1024,
  "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
  "objects": [], "statistics": { "objects": 144, "classes": ["0"] } }
```

- `image_url` and `mask_url` are absolute `data.*` addresses with no key. They are the only way to get
  bytes; the gateway never carries them.
- `width` and `height` are the image's own pixels, which is what `bbox` is measured in.
- `objects` is absent from a **list** response and present in a **detail** response; `statistics`
  summarises a row inside a list, so you can choose rows without fetching each one.
- A row also carries whatever the dataset annotates it with — location, growth stage, tags — and those
  are exactly the filter keys the dataset publishes.

## Where each thing comes from

| what you show | where it comes from |
|---|---|
| the image | `image_url`, served by `data.global-wheat.com`, no key |
| the mask | `mask_url`, same, where the dataset has one |
| a box on the image | `objects[].bbox` — `[x, y, w, h]` in the image's pixels |
| a class or a colour | `classes[].name` and `classes[].color` where the dataset publishes a palette; `objects[].category` on a row |
| segments of one object | `objects[].rle`, decoded to a mask the size of the image |
| how many objects | `statistics.objects` in a list, the length of `objects` in a detail |
| what you can filter by | the `filters` block of `GET /datasets/<id>`, with the values it actually holds |

## Filters

`GET /datasets/<id>` publishes a `filters` block: every key its rows can be narrowed by, and the
values that actually occur — no guessing whether `Australia` is spelled `Australia`. Send them as query
parameters (`?split=train`), repeat a key to narrow further (`?split=train&split=val` means both), and
walk the pages with the `next_cursor` a list response reports.

The three pages that follow are one dataset each: what a row holds, one real response, and the fields
that are easy to misread.
