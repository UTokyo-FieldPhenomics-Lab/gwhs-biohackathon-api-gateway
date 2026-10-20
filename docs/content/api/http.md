# Raw HTTP

Every call is one HTTP request: a key in one header, JSON in and JSON out. Nothing has to be
installed, and every client on this site is a convenience over what is on this page.

## The contract

| | |
|---|---|
| `https://api.global-wheat.com` | everything dynamic: the datasets, their annotations, model runs |
| `X-API-Key: <your team's key>` | on every request except `/health` |
| `https://data.global-wheat.com` | the bytes — an `image_url` or `mask_url` in a response, no key |

A response that points at an image carries an absolute `data.*` address: the gateway never carries
bytes, so displaying an image is a request to the CDN and not to us.

## Every route

| call | what it is |
|---|---|
| `GET /health` | liveness and version — the one route without a key |
| `GET /datasets` | the directory: one line per dataset |
| `GET /datasets/<id>` | that dataset's contract: fields, filters, classes, palette |
| `GET /datasets/<id>/images` | a page of rows — addresses and counts, never the annotations |
| `GET /datasets/<id>/images/<row>` | one row's whole annotation |
| `GET /models` | the models, the parameters each accepts, and the machines behind them |
| `GET /models/<id>` | one model, with its parameters and its machines |
| `POST /models/<id>/predict` | run a model on a published row |
| `POST /models/<id>/predict/upload` | run a model on your own photograph |

The complete list — every parameter, every field of every answer, and a form that sends the call —
is the [Base API reference](reference.md). The table above is the map, not the manual.

## Four calls

```bash
# the directory
curl -s -H "X-API-Key: $KEY" "$API/datasets"

# one row's annotation
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhs-temp/images/0"

# a model on one published row: the gateway resolves the address, you never send a URL
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"dataset": "gwhs-temp", "row": 0}' \
  "$API/models/sam3.1-meta/predict?prompt=wheat%20head"

# a model on your own photograph: the file itself, as the multipart field `file`
curl -s -H "X-API-Key: $KEY" -F "file=@photo.jpg" \
  "$API/models/sam3.1-meta/predict/upload?prompt=wheat%20head"
```

A page of rows reports `next_cursor`; send it back as `cursor` for the next page, and stop when it is
`null`. Filters are query parameters, and a key sent twice narrows the result rather than widening it:
`?split=train&split=val` means both, never either.

### Resizing before you send

The upload route takes the file as the multipart field `file`, and **the model resizes whatever it
receives to the side it runs at**: 1008 px for `sam3.1-meta`, 512 px for `gwfss-segformer-b4`. That
side is the model's own and `GET /models/<id>` publishes it as `resolution`. A larger photograph buys
nothing — the pixels beyond that side are discarded before the network sees them.

Send it at about that side. Pillow is usually already there, because it is what displays an image:

```python
from io import BytesIO
from PIL import Image, ImageOps

with Image.open("photo.jpg") as photo:
    photo = ImageOps.exif_transpose(photo)   # a phone's EXIF orientation, applied to the pixels
    photo.thumbnail((1008, 1008))            # 512 for gwfss-segformer-b4
    prepared = BytesIO()
    photo.convert("RGB").save(prepared, "JPEG", quality=90)

# prepared.getvalue() is what goes in the multipart field `file`
```

The clients on this site do this for you: `predict_file` resizes by default and sends the original
bytes untouched with `resize_before_submit=False`. The run-a-model page does the same, and its
**resize before sending** box is the way to send a photograph whole.

## Refusals

Every refusal is one body, whichever route produced it:

```json
{"error": {"code": "invalid_request", "message": "row 99999 is past the end of gwhd"}}
```

| status | code | what happened |
|---|---|---|
| `400` | `invalid_request` | the request's own fault: a row that does not exist, a parameter outside its range, bytes that are not an image. The `message` names what was refused. |
| `401` | `invalid_key` | the key is wrong, or missing |
| `404` | `not_found` | no such dataset or model |
| `413` | `payload_too_large` | an upload over 15 MB — [resize it](#resizing-before-you-send) first |
| `429` | `quota_exceeded` | three inferences in flight already from this key, or the upload link has no room for this body; nothing was queued, and `Retry-After` says when to come back |
| `503` | `unavailable` | no machine is answering this model. Safe to retry |
| `504` | `upstream_timeout` | the model took too long. Safe to retry |

Nothing is retried for you. `503` and `504` are worth another call, `400` never is, and a client that
guessed would hide which of the two happened.

## Model parameters

A model's parameters are **that model's own**, named as its own documentation names them, and listed
by `GET /models/<id>` with their defaults and bounds. There is no wrapper vocabulary: SAM 3.1 declares
`prompt`, `boxes`, `points`, `confidence_threshold`, `imgsz`; another model may declare something
else entirely, and a name it did not declare is a `400` whose message lists what it does accept.

They travel as **flat query parameters** on both predict routes, one value each:

```bash
"$API/models/sam3.1-meta/predict?prompt=wheat%20head&confidence_threshold=0.25"
```

### Geometry: `boxes` and `points`

A geometry parameter is **one JSON array in one parameter**, in the coordinates of the image the
model was given — the image's own pixels, top-left origin, whatever its size:

| parameter | one entry | example |
|---|---|---|
| `boxes` | `[x, y, w, h, label]` | `[[480, 290, 110, 360, 1]]` |
| `points` | `[x, y, label]` | `[[256, 300, 1], [40, 40, 0]]` |

`label` is `1` for a positive entry and `0` for a negative one, and it may be left out — the default
is positive. Order is kept, so a negative entry applies after the positive ones before it. The
coordinates are the same `[x, y, width, height]` convention the answer's `bbox` uses, so a box you
read from an answer can be sent back unchanged.

A list is **never** split into repeated parameters: `?boxes=1&boxes=2` is a `400`, because
`?split=train&split=val` means something on the dataset routes and nothing here. Send one array.

### `imgsz`

A size parameter is an enumerated list, not a range: `GET /models/<id>` publishes the sides the model
was actually run at. A value above the list is **not** refused — the model runs at its ceiling and
says so, and `resolution_used` tells you the side that ran either way:

```json
{ "resolution_used": 896,
  "warnings": [{"code": "resolution_clamped", "message": "imgsz clamped: …"}],
  "objects": [ … ] }
```

`warnings` is absent from an answer that needed none. A value below the smallest published side is a
`400`: the model was never run there, and the answer would be neither fast nor meaningful.

## Limits

| what | limit | over it |
|---|---|---|
| inferences in flight, per key | 3 | `429`, not a queue |
| uploaded bytes in flight, the whole pool | about 24 MB | `429` with `Retry-After`, after waiting up to 20 s |
| any other request | none | — |
| an uploaded image | 15 MB | `413` |
| an uploaded image, decoded | 50 megapixels | `400` |

The three are three across the whole team, not three per person: one key is one team's share of the
GPU pool. **Only the two predict routes count.** A dataset listing, a row's detail and the model
directory are answered from memory and queue behind no machine, so they are never refused for being
one request too many; a page that loads several of them at once is not competing with anybody's
inference.
