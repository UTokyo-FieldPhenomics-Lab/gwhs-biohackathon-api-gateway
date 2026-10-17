# Datasets

One row is one image and its annotation, addressed as `(dataset, row)`. Three datasets ship for the
event, and the API answers the first ten rows of each published one today — the full sets follow
before the hackathon.

| id | dataset | one row carries |
|---|---|---|
| `gwhd` | **Global Wheat Head Detection** | an image and a bounding box per wheat head |
| `gwfss` | **Global Wheat Full Semantic Organ Segmentation** | an image and a mask, one colour per organ |
| `gwhs` | **Global Wheat Head Segmentation** | an image and one object per spikelet, with box and mask geometry |

## `gwhd` — Global Wheat Head Detection

![One gwhd row with every published head boxed](gwhd-row0-boxes.jpg)

Row 0 of the demo set: a 1024×1024 plot photograph with **one box per wheat head**, 144 of them, drawn
here in pink. The highlighted one is the row's first object. CC-BY-4.0.

```bash
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhd/images?limit=2"
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhd/images/0"
```

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

`bbox` is `[x, y, w, h]` in the image's own pixels, which is what the picture above draws:

![How bbox maps onto the image](bbox-diagram.png)

- `category` is `"0"` here: the upstream named no head class, so the box is classified by the upstream
  integer, returned as text rather than dressed up as a name.
- A row also carries `domain`, `country`, `location`, and `development_stage`, and those are exactly
  the filter keys `GET /datasets/gwhd` publishes beside `split` and `category`.
- Row 0 holds 144 boxes. A list response reports that as `statistics.objects`, so a client can choose
  rows by what is in them without fetching an annotation.

## `gwfss` — Global Wheat Full Semantic Organ Segmentation

|                    the image                     |                    its mask                     |
| :----------------------------------------------: | :---------------------------------------------: |
| ![A gwfss row](gwfss-row0-image.jpg) | ![Its mask](gwfss-row0-mask.png) |

One row is a 512×512 image plus **a mask image of the same size**, and the mask *is* the annotation:
three channels, one colour per organ, painted in the palette the dataset describes. CC-BY-4.0.

```bash
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwfss"
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwfss/images/0"
```

```json
{
  "dataset": "gwfss", "row": 0, "split": "labelled", "width": 512, "height": 512,
  "image_url": "https://data.global-wheat.com/gwfss/images/00000000.png",
  "mask_url": "https://data.global-wheat.com/gwfss/masks/00000000.png"
}
```

A detail call returns both URLs and no objects, because there are none to return. What the palette
means comes from the dataset's own description — `GET /datasets/gwfss` — and nowhere else:

| class | colour | in row 0 |
|---|---|---|
| `background` | `#000000` | 146,817 px |
| `head` | `#32ff84` | 0 px |
| `stem` | `#3284ff` | 1,920 px |
| `leaf` | `#d6ff32` | 113,407 px |

Split those pixels per class and each class becomes one boolean array — that is
[the client](../clients/index.md)'s `label_masks`. In a list response, `statistics.classes`
names the classes a row actually contains, so row 0 holds `stem` and `leaf` and the zero beside `head`
is a fact about the image rather than a gap in the data.

## `gwhs` — Global Wheat Head Segmentation

![One instance row: the image, its boxes, and the decoded masks](gwhs-row0-annotation.jpg)

Not published yet: the full annotation follows the dataset release, so `GET /datasets/gwhs` answers
nothing today. The figure above, and the object below, are a demo row of the same schema — a crop of a
592×2509 photograph of one ear, 24 objects, each with a box and the mask its geometry decodes to.

```bash
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhs/images/0"
```

One entry of that response's `objects`:

```json
{ "idx": 0, "category": "spikelet_-_side1", "bbox": [231.0, 923.0, 126.0, 162.0],
  "area": 12074.0, "attributes": ["Green-Spikelet"],
  "rle": { "size": [2509, 592], "counts": "Zofa05S…" } }
```

Every object carries a box, a **run-length mask**, an area, and its attributes, and the row carries its
tags. Where `gwhd` returns one box per head, this returns one box *and one mask per spikelet*, which is
what makes counts and sizes per spikelet possible. Decoding `rle.counts` is
not something you write: [the client](../clients/index.md) hands you the mask.

## How a response maps onto what you display

| what you show | where it comes from |
|---|---|
| the image | `image_url`, served by `data.global-wheat.com`, no key |
| the mask | `mask_url`, same, where the dataset has one |
| a box on the image | `objects[].bbox` — `[x, y, w, h]` in the image's pixels |
| a class or a colour | `classes[].name` and `classes[].color` where the dataset publishes a palette; `objects[].category` on a row |
| segments of one object | `objects[].rle`, decoded to a mask the size of the image |
| how many objects | `statistics.objects` in a list, the length of `objects` in a detail |
| what you can filter by | the `filters` block of `GET /datasets/<id>`, with the values it actually holds |
