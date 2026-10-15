# Global Wheat BioHackathon API

Build an agricultural application **without downloading the dataset, deploying a model, or owning a
GPU**. Two calls carry the whole experience: fetch one image, and get one model result.

| | |
|---|---|
| Dataset plane | **live** — the directory, one dataset's contract, its images, and one row's annotation |
| Image plane | **live** — `https://data.global-wheat.com`, no key, long cache |
| Model plane | **not reachable yet** — routing an inference request to a GPU backend is still being wired |

## Call it

Every request except `/health` needs your team's token in the `X-API-Key` header. Tokens are issued
per team, so ask the organisers for yours.

```bash
export API=https://api.global-wheat.com
export GWHS_API_KEY=<your team's token>

curl -s "$API/health"                                               # open: liveness and version
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets"               # every dataset, one line each
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets/gwhd"          # one dataset's contract
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets/gwhd/images?limit=2"    # a page of images
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets/gwhd/images/0"          # one row's annotation
```

## What comes back

A list of images carries addresses and counts, not annotations — a page stays small whatever the
dataset:

```json
{
  "dataset": "gwhd", "count": 2, "next_cursor": "eyJvIjoxMDB9",
  "images": [
    { "row": 0, "split": "train", "width": 1024, "height": 1024,
      "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
      "statistics": { "objects": 47 } }
  ]
}
```

One row's annotation is one call, and its shape is the dataset's shape — a detection dataset returns
boxes:

```json
{
  "dataset": "gwhd", "row": 0, "split": "train", "width": 1024, "height": 1024,
  "image_url": "https://data.global-wheat.com/gwhd/images/00000000.jpg", "mask_url": null,
  "objects": [ { "idx": 0, "category": "0", "bbox": [936, 4, 41, 78] } ]
}
```

A semantic dataset returns no objects at all: its annotation **is** an image, and `mask_url` is it.

```json
{
  "dataset": "gwfss", "row": 0, "split": "labelled", "width": 512, "height": 512,
  "image_url": "https://data.global-wheat.com/gwfss/images/00000000.png",
  "mask_url": "https://data.global-wheat.com/gwfss/masks/00000000.png"
}
```

`gwhs` adds object-level RLE geometry, tags, and attributes to that same detail call;
[the examples](examples/index.md) decode one.

## Reading an address

- An address is `(dataset, row)` — `/datasets/<dataset>/images/<row>` — where the row is that
  dataset's absolute row number. Row numbers repeat across datasets, so a row number alone means
  nothing.
- `image_url` and `mask_url` point at `https://data.global-wheat.com` and need no key: display them
  directly. The API never returns image bytes.
- Lists are paged with `limit` (default 100, at most 1,000) and `next_cursor`. Send the cursor back
  unchanged; it is valid only beside the filters that produced it.
- Which fields and filters a dataset offers, and the class names and palette of its annotation, come
  from `GET /datasets/<dataset>`. Read them there instead of hard-coding a class list: fetch it once
  per session and keep it.

## The datasets

| id | one row is | published |
|---|---|---|
| `gwhd` | an image and its wheat-head bounding boxes, CC-BY-4.0 | live |
| `gwfss` | an image and a semantic mask, painted in the upstream's four-colour palette, CC-BY-4.0 | live |
| `gwhs` | an image and object-level RLE masks with tags and attributes, CC-BY-4.0 | when the full annotation is released |

## Next

- [Examples](examples/index.md) — decoding an RLE, reading a palette mask
- [Interactive API reference](https://api.global-wheat.com/docs) — every route, with a try-it button
