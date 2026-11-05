---
name: global-wheat-api
description: Use the Global Wheat BioHackathon API for wheat image data, segmentation, detection, counting, feature extraction, and hosted image generation. Use when the user asks for a dataset row, a model prediction, a mask, a count, features, or a generated image from this API.
---

# Global Wheat BioHackathon API

Use the API at `https://api.global-wheat.com` for wheat and plant images. Every route except
`GET /health` needs the team key in `X-API-Key`; read it from `GLOBAL_WHEAT_API_KEY` and never print
it.

This is an orientation, not a frozen copy of the contract. Read `GET /models/<id>` and the OpenAPI
before a call you have not made before; the API is the authority.

## Discover first

- `GET /datasets` — ids, rows, splits, annotation shape.
- `GET /datasets/<id>` — fields, filters, classes, palette.
- `GET /models` — ids, families, parameters, `backends_up`.
- `GET /models/<id>` — one model's parameters and machines.
- `GET /openapi.json` — the full contract.

Do not hardcode row counts or model lists; the API changes. `gwhs` is not served yet; use
`gwhs-temp` for a development row. `gwhd` and `gwfss` are published in full.

## Read next

| when you need | read |
|---|---|
| the exact request or response schema | `https://api.global-wheat.com/openapi.json` |
| a model's parameters, defaults and bounds | `GET /models/<id>` |
| a dataset's fields, filters and palette | `GET /datasets/<id>` |
| the HTTP policy, refusals and limits | `https://docs.global-wheat.com/api/http/` |
| a model's behavior and measurements | `https://docs.global-wheat.com/models/` |
| a client that decodes masks and grids | `https://docs.global-wheat.com/api/clients/` |

## Address data as (dataset, row)

A row is the absolute row of the pinned upstream revision. `GET /datasets/<id>/images?limit=…`
pages; `next_cursor` goes back as `cursor`. A row's `image_url` and `mask_url` are public `data.*`
addresses with no key; bytes never come from `api.*`.

## Choose a model

| the user wants | use |
|---|---|
| text-, box- or point-prompted masks | `sam3.1-meta` |
| open-vocabulary instance masks | `wevisone-vl-4b` |
| semantic organ masks | `gwfss-segformer-b4`, `sprout-l-gwfss` |
| a count from exemplar boxes | `tasselnetv4` |
| dense features or a CLS vector | `dinov3-vitl16`, `dinov3-vith16plus` |
| COCO boxes or instance masks | `yolo26-ultralytics`, `yolo26-seg-ultralytics` |
| prose, a picture or an edit of one | `internvl-u` |
| a prompt-only picture | `nano-banana-2.1`, `nano-banana-2`, `nano-banana-pro` |

This table is current, not exhaustive: `GET /models` is the live list.

## Run a model

`POST /models/<id>/predict` with JSON `{"dataset": "...", "row": 0}` and the model's parameters in
the query. `POST /models/<id>/predict/upload` takes a multipart `file` for your own image.
`POST /models/<id>/generate` takes only a query (`prompt`, …) and is only for the Nano Banana ids;
it spends image credits, so ask the user before calling it.

Parameters are the model's own; `GET /models/<id>` lists names, defaults and bounds. A geometry
parameter is one JSON array in one parameter: `boxes=[[x,y,w,h,label], …]` or
`points=[[x,y,label], …]`. An unknown name or a repeated one is a `400`.

### A minimal call

```bash
export API=https://api.global-wheat.com

curl -s -H "X-API-Key: $GLOBAL_WHEAT_API_KEY" "$API/datasets"

curl -s -H "X-API-Key: $GLOBAL_WHEAT_API_KEY" -H "Content-Type: application/json" \
  -d '{"dataset": "gwhs-temp", "row": 0}' \
  "$API/models/sam3.1-meta/predict?prompt=wheat%20head"
```

## Read the answer

`objects` is present; it may be empty. An object has `category`, `score`, `bbox` `[x,y,w,h]` in the
image's pixels, `area`, and `rle` (COCO compressed) where a mask exists. A counting model adds
`count` and `density` (`size` `[rows,columns]`, `values` row-major); a backbone adds `embedding` and
`features`; a prose/picture model adds `answer` and/or `image`. `resolution_used`, `computed_by`
and `warnings` describe the run.

Use the client files from `https://docs.global-wheat.com/api/clients/` to decode RLE masks, density
grids, feature grids and pictures; do not hand-roll the COCO decoder. Python: `global_wheat.py`;
JavaScript: `global-wheat.js` plus `.web.js`/`.node.js`; Java: `GlobalWheat.java`.

## Do not

- Print or transmit the key anywhere except `api.global-wheat.com`.
- Call `generate` without the user's consent; it spends credits.
- Invent a row, a dataset or a parameter; use `GET /datasets` and `GET /models/<id>`.
- Build a `data.*` URL from client input; use the `image_url`/`mask_url` the API returned.
- Use `gwhs`; it answers `404` until its annotation is released.

## Limits and failures

- Three inferences per key; metadata reads take none.
- `429`: wait and retry after `Retry-After`; `502`/`503`/`504`: one retry is reasonable; `400`:
  fix the request, do not retry.
- Uploads: 15 MB, 50 megapixels; some models cap lower (YOLO26-seg 4 MP, WeVisOne 4 MP).

See `https://docs.global-wheat.com/api/http/` for the full policy and
`https://api.global-wheat.com/openapi.json` for the schema.
