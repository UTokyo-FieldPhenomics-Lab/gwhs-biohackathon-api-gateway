# Global Wheat BioHackathon API

Build an agricultural application **without downloading the dataset, deploying a model, or owning a
GPU**. Two calls carry the whole experience: fetch one image, and get one model result.

| | |
|---|---|
| Dataset plane | **live** — what each dataset holds, its images, and one row's annotation |
| Image plane | **live** — `https://data.global-wheat.com`, no key, long cache |
| Model plane | **live** — one segmentation model: send an image address or your own photograph, get boxes and masks back |

## Call it

Every route except `/health` needs your team's token in the `X-API-Key` header. Tokens are issued per
team, so ask the organisers for yours. In the [interactive reference](interactive.md) you can paste it
into the field above the call list and try every call from there — the page sends the header for you
and remembers the key in your browser.

```bash
export API=https://api.global-wheat.com
export KEY=<your team's token>

curl -s "$API/health"
curl -s -H "X-API-Key: $KEY" "$API/datasets"
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhd"
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhd/images?limit=2"
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhd/images/0"
```

| call | what it is |
|---|---|
| `/health` | liveness and version — the one route that needs no key |
| `/datasets` | every dataset, one line each |
| `/datasets/<id>` | that dataset's contract: its fields, filters, classes, and palette |
| `/datasets/<id>/images` | a page of rows — addresses and counts, never the annotations themselves |
| `/datasets/<id>/images/<row>` | one row's annotation |

Every address is `(dataset, row)`, and rows repeat across datasets, so a row number on its own means
nothing. A response's `image_url` and `mask_url` point at `https://data.global-wheat.com` and need no
key, so display them directly: the API never carries image bytes.

## The datasets

| id | one row is |
|---|---|
| `gwhd` | an image and its wheat-head bounding boxes — CC-BY-4.0 |
| `gwfss` | an image and a semantic mask, one colour per class — CC-BY-4.0 |
| `gwhs` | an image and object-level RLE masks with tags and attributes — CC-BY-4.0, released once the full annotation is |

The API answers the **first ten rows** of each published dataset today, which is enough to build
against. The full sets are published before the event, and the same calls then return every row.

## Next

- [Datasets](datasets/index.md) — what each one holds, and how a response maps onto the image
- [Models](models/index.md) — the model you can call, what it accepts, and one you can run from this site
- [Clients](clients/index.md) — one file per language, so a call is a function and a mask is decoded
- [Interactive API reference](interactive.md) — every route, with a try-it button, without leaving this site
