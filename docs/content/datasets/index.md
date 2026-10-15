# Datasets

One row is one image and its annotation, addressed as `(dataset, row)`. Three datasets ship for the
event, and the API answers the first ten rows of each published one today — the full sets follow
before the hackathon.

## `gwhd` — wheat heads as boxes

![A gwhd row: a wheat plot photographed from above](https://data.global-wheat.com/gwhd/images/00000000.jpg)

One row is a field image, 1024×1024 JPEG, with **one bounding box per wheat head**. The dataset is
CC-BY-4.0, as every dataset here is. `GET /datasets/gwhd/images/0` answers row 0's boxes, and the
values are the image's own pixels — they draw straight onto the image `image_url` points at:

```json
{
  "dataset": "gwhd", "row": 0, "split": "test", "width": 1024, "height": 1024,
  "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
  "domain": "UQ_9", "country": "Australia", "location": "Brookstead",
  "development_stage": "Filling - Ripening",
  "objects": [
    { "idx": 0, "category": "0", "bbox": [227.0, 3.0, 58.0, 47.0] },
    { "idx": 1, "category": "0", "bbox": [204.0, 31.0, 21.0, 25.0] }
  ]
}
```

- `bbox` is `[x, y, w, h]` in pixels, so `[227, 3, 58, 47]` is the rectangle from (227, 3) to
  (285, 50).
- `category` is `"0"` here: the upstream named no head class, and the gateway returns the integer it
  labelled with, as text, rather than inventing a name.
- Row 0 holds **144** boxes. In a list response that count is `statistics.objects`, so a client can
  choose rows by what is in them without fetching an annotation.
- `domain`, `country`, `location`, and `development_stage` are on the row, and they are exactly the
  filter keys `GET /datasets/gwhd` offers beside `split` and `category`.

## `gwfss` — a semantic mask

|                         the image                          |                        the mask                         |
| :--------------------------------------------------------: | :-----------------------------------------------------: |
| ![A gwfss row](https://data.global-wheat.com/gwfss/images/00000000.png) | ![Its mask](https://data.global-wheat.com/gwfss/masks/00000000.png) |

One row is a 512×512 image plus a mask image of the same size. **The mask is the annotation**: three
channels, one colour per class, and the palette is the dataset's own — `GET /datasets/gwfss`
publishes it and nothing else does.

| class | colour | in row 0 |
|---|---|---|
| `background` | `#000000` | 146,817 px |
| `head` | `#32ff84` | 0 px |
| `stem` | `#3284ff` | 1,920 px |
| `leaf` | `#d6ff32` | 113,407 px |

A detail call answers both URLs and no objects, because the mask *is* the annotation:

```json
{
  "dataset": "gwfss", "row": 0, "split": "labelled", "width": 512, "height": 512,
  "image_url": "https://data.global-wheat.com/gwfss/images/00000000.png",
  "mask_url": "https://data.global-wheat.com/gwfss/masks/00000000.png"
}
```

Reading those pixels per class is [Post-processing](../post-processing/index.md)'s first job. In a list
response, `statistics.classes` names the classes a row actually contains — row 0 holds `stem` and
`leaf`, which is why `head` counts zero above: no head was annotated in that image, and a colour is
never present without being meant.

## `gwhs` — objects, with RLE geometry

Not published yet: the full annotation is released after the dataset release, so
`GET /datasets/gwhs` answers nothing today. What it will hold is one row per image, and next to each
object's box: a run-length mask for that object, its tags, and its attributes. The
[post-processing](../post-processing/index.md) snippets decode that geometry, and they are the same code
you will run on it.

## How a response maps onto what you display

| what you show | where it comes from |
|---|---|
| the image | `image_url`, served by `data.global-wheat.com`, no key |
| the mask | `mask_url`, same |
| a box on the image | `objects[].bbox` — `[x, y, w, h]` in the image's pixels |
| a class name | `classes[].name` and `classes[].color` where the dataset publishes a palette; `objects[].category` on a row |
| how many objects | `statistics.objects` in a list, the length of `objects` in a detail |
| what you can filter by | the `filters` block of `GET /datasets/<id>`, with the values it actually holds |
