# Global Wheat BioHackathon API

Build an agricultural application **without downloading the dataset, deploying a model, or owning a
GPU**. Two calls carry the whole experience: fetch one image, and get one model result.

Everything is HTTPS, and every address is `(dataset, row)` — a dataset id plus the absolute row number
of the pinned upstream revision. A row number on its own means nothing, because rows repeat across
datasets.

## What answers today

| | |
|---|---|
| Dataset plane | **live** — the directory, one dataset's contract, its images, and one row's annotation |
| Image plane | **live** — `https://data.global-wheat.com`, no key, long cache |
| Model plane | **not reachable yet** — routing an inference request to a GPU backend is still being wired |

## Get a token

`/health` and this site are open; everything else needs a participant token in the `X-API-Key` header.
Tokens are issued per team, so ask the organisers for yours.

## Call it

```bash
export API=https://api.global-wheat.com
export GWHS_API_KEY=<your team's token>

curl -s "$API/health"                                              # open: liveness and version
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets"              # the directory
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets/gwfss"        # one dataset's contract
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets/gwfss/images?limit=2"
curl -s -H "X-API-Key: $GWHS_API_KEY" "$API/datasets/gwfss/images/0"   # one row's annotation
```

Fetch a dataset's contract once per session: it carries the class names, the palette, and which
filters that dataset actually offers, and reading them from the API is what keeps a class list out of
your source code.

The `image_url` and `mask_url` a response returns point straight at `data.global-wheat.com`, and those
URLs are what you display: **the API never carries bytes**. A mask is a URL in the metadata plane and
an object in the image plane, never a body in a JSON response.

To try it from a browser, [the interactive reference](https://api.global-wheat.com/docs) is FastAPI's
own Swagger UI. It sends the same header.

## The datasets

| id | one row is | published |
|---|---|---|
| `gwhd` | an image and its bounding-box annotations, CC-BY-4.0 | live |
| `gwfss` | an image and a semantic mask, painted in the upstream's four-colour palette, CC-BY-4.0 | live |
| `gwhs` | an image and object-level RLE masks with attributes, CC-BY-4.0 | when the full annotation is released |
| `gwhs-temp` | ten rows of the `gwhs` schema, for building against before then | development only |

## Next

- [Data layout](data-layout.md) — the contract: object layout, addressing, and the index fields
- [Examples](examples/index.md) — decoding an RLE, reading a palette mask
- [Interactive API reference](https://api.global-wheat.com/docs) — every route, with a try-it button
