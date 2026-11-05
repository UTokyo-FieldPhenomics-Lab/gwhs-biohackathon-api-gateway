# Raw HTTP

Every call is one HTTP request: a key in one header, then JSON, a query string or a file — and JSON
back. Nothing has to be installed, and every client on this site is a convenience over what is on this
page.

## The contract

| item | what it is |
|---|---|
| `https://api.global-wheat.com` | everything dynamic: the datasets, their annotations, model runs |
| `X-API-Key: <your team's key>` | on every data call; `/health`, `/docs`, `/play` and `/openapi.json` need none |
| `https://data.global-wheat.com` | the bytes — an `image_url` or `mask_url` in a response, no key |

A response that points at an image carries an absolute `data.*` address: the gateway never carries
dataset bytes, so displaying an image is a request to the CDN and not to us. Uploads and generated
pictures are the exception — they travel through `api.*` because they are part of the request or the
answer.

## Every participant route

| call | what it is |
|---|---|
| `GET /health` | liveness and version — the one route without a key |
| `GET /datasets` | the directory: one line per dataset |
| `GET /datasets/<id>` | that dataset's contract: fields, filters, classes, palette |
| `GET /datasets/<id>/images` | a page of images — addresses and counts, never the annotations |
| `GET /datasets/<id>/images/<row>` | one image's whole annotation |
| `GET /models` | the models, the parameters each accepts, and the machines behind them |
| `GET /models/<id>` | one model, with its parameters and its machines |
| `POST /models/<id>/predict` | run a model on a published image |
| `POST /models/<id>/predict/upload` | run a model on your own photograph |
| `POST /models/<id>/generate` | make a picture from a prompt, with no image to address |

The complete list — every parameter, every field of every answer, and a form that sends the call —
is the [Live API reference](../reference.md). The table above is the map, not the manual.

## Five calls

Set the two variables once, then copy any block below:

```bash
export API=https://api.global-wheat.com
export KEY='YOUR_TEAM_KEY'      # replace with the key the organisers sent you
```

```bash
# the directory
curl -s -H "X-API-Key: $KEY" "$API/datasets"

# one image's annotation
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhs-temp/images/0"

# a model on one published image: the gateway resolves the address, you never send a URL
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"dataset": "gwhs-temp", "row": 0}' \
  "$API/models/sam3.1-meta/predict?prompt=wheat%20head"

# a model on your own photograph: the file itself, as the multipart field `file`
curl -s -H "X-API-Key: $KEY" -F "file=@photo.jpg" \
  "$API/models/sam3.1-meta/predict/upload?prompt=wheat%20head"

# a Nano Banana model: the prompt is the whole request, no body and no image
curl -s -X POST -H "X-API-Key: $KEY" \
  "$API/models/nano-banana-2.1/generate?prompt=a%20wheat%20field%20at%20golden%20hour&aspect_ratio=16:9&image_size=1K" \
  -o answer.json
```

A page of images reports `next_cursor`; send it back as `cursor` for the next page, and stop when it is
`null`. Filters are query parameters, and a key sent twice narrows the result rather than widening it:
`?split=train&split=val` means both, never either.

### Uploads

The upload route takes the file as the multipart field `file`, and **the model resizes whatever it
receives to the side it runs at**. That side is the model's own, and `GET /models/<id>` publishes it
as `resolution`; 1008 px for `sam3.1-meta` and 512 px for `gwfss-segformer-b4` are two examples. A
larger photograph buys no resolution, and a smaller one is upscaled; sending it at about that side
saves upload time.

The clients do this for you: `predict_file` resizes by default, and
`resize_before_submit=False` sends the original bytes. The [live demo](../../examples/demo.md) has
the same switch. If you write the request yourself, `-F "file=@photo.jpg"` is the whole of it.

## Refusals

Every refusal is one body, whichever route produced it:

```json
{"error": {"code": "not_found", "message": "no such row"}}
```

| status | code | what happened |
|---|---|---|
| `400` | `invalid_request` | the request's own fault: a missing or unknown parameter, geometry that does not parse, bytes that are not an image. The `message` names what was refused. |
| `401` | `invalid_key` | the key is wrong, or missing |
| `404` | `not_found` | no such dataset, model, or image |
| `413` | `payload_too_large` | an upload over 15 MB — the clients resize before sending; otherwise [downscale it](#uploads) first |
| `429` | `quota_exceeded` | this key already holds its three inferences, the host is receiving too many uploads, or a model backend refused for its own capacity or allowance. The message says which; `Retry-After` is present when waiting helps |
| `502` | `upstream_error` | a machine answered that it could not serve this request, or every machine for the model failed. Worth another call; one that keeps failing is worth reporting |
| `503` | `unavailable` | no machine is answering this model. Retry later |
| `504` | `upstream_timeout` | the model took too long, or an upload could not be delivered to it in time. Retry later |

The clients do not automatically retry a failed call. The gateway may try another backend within
the same request before returning an error. `502`, `503` and `504` are worth one more call; `400`
requires correcting the request. A `429` that names credits or the shared budget does not clear by
retrying. A retried `generate` is a new picture and a new charge, because a generation is never
cached.

## Model parameters

A model's parameters are **that model's own**, named as its own documentation names them, and listed
by `GET /models/<id>` with their defaults and bounds. There is no wrapper vocabulary: SAM 3.1 declares
`prompt`, `boxes`, `points`, `confidence_threshold`, `imgsz`; another model may declare something
else entirely, and a name it did not declare is a `400` whose message lists what it does accept.

They travel as **flat query parameters** on the model-call routes — predict, upload and generate —
one value each:

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

### Counting results

A counting model reports a number instead of masks. Its answer carries `count` and `density`, and
`objects` is empty:

```json
{ "model": "tasselnetv4", "resolution_used": 384,
  "objects": [],
  "count": 21.7,
  "density": { "size": [23, 23], "values": [0.041, 0, 1.284, …] } }
```

`density` is the field the count was summed from, on the model's own grid: `size` is
`[rows, columns]`, `values` are the cells' local counts read row by row, and they add up to `count`.
One cell is a counting window, not a pixel, so read `size` rather than a constant: the grid is square
per tile and as wide as the picture. Read `count` as the answer, and draw the field as a heatmap if
you want to see where the mass sits.

The field is not a segmentation. A cell holding 1.4 objects says how much sits in that window; it does
not say where one object ends and the next begins, and nothing in the answer carries an object's
identity. For per-plant outlines or boxes, use a segmentation model, such as `gwfss-segformer-b4` or
`sam3.1-meta`.

A point-counting model would answer `points` instead of `density` — `[x, y, score]` in the image's own
pixels. No model on this service answers that way today.

An answer may also carry `warnings`, in the same shape a clamped `imgsz` produces. A counting model
reports one when an exemplar is larger than the finest window it can count in — a spike that fills the
frame — and the count is then a coarse one. The answer is an answer; the warning says which one.

A counting model's prompt is **exemplar boxes**: `boxes`, one array of `[x, y, w, h, 1]`, in the form
[Geometry](#geometry-boxes-and-points) describes. Draw a few around examples of what you want counted;
the model matches their appearance across the picture. Without them it has nothing to count.

### Backbone results

A backbone has no head, so it reports no objects, no count and no points. It answers its own tokens,
and `objects` is present and empty for the same reason a counting answer's is:

```json
{"model": "dinov3-vitl16", "resolution_used": 512,
 "objects": [],
 "embedding": [-1.10202, 0.27331, "…"],
 "features": {"size": [32, 32], "dim": 1024, "dtype": "float16", "data": "AAAA…"}}
```

`embedding` is the CLS token: one float per dimension, the whole-image descriptor. `features` is the
patch grid; `size` is the frame the network ran at divided by the patch side — 16 — **not your
image's size**, so a 4:6 picture at 512 gives `[32, 21]`. `dim` is 1024 on `dinov3-vitl16` and 1280
on `dinov3-vith16plus`, and the two are different spaces; do not compare them. `data` is base64 of
the grid, row-major and channels-last, little-endian float16. The clients decode it to a
`(rows, columns, dim)` array; over plain HTTP, base64-decode it, reinterpret the bytes as
little-endian float16, and reshape to `size + [dim]`.

The vectors are not normalized; use cosine, not a raw dot product. `side` is the longer side of the
frame and preserves the aspect ratio, each dimension floored to a multiple of the patch; a value
above the declared maximum is answered at it with `resolution_clamped`, because the answer's size is
the constraint. [The backbone page](../../models/dinov3.md) explains what the tokens are and what
can be built on them; the fields here are the whole contract.

### Prose and picture results

`internvl-u` and the Nano Banana ids answer in words or pixels rather than geometry. They still
return `objects: []`, so one client parses their answer with the same code as a segmenter's, and they
carry two optional fields beside it:

```json
{ "model": "nano-banana-2.1", "objects": [],
  "answer": "Here is a photorealistic shot of a single wheat spike against dark soil.",
  "image": {"media_type": "image/png", "width": 1376, "height": 768,
            "data": "iVBORw0KGgo…"} }
```

`answer` is prose the model wrote, absent when it wrote none. `image` is the encoded file itself:
`media_type`, its own `width` and `height`, and base64 of the bytes in `data`. The clients decode it
to a picture and keep the raw fields beside it. `internvl-u` chooses prose, a picture or both with
its `mode` parameter; the Nano Banana ids answer a picture.

A Nano Banana **edit** at `aspect_ratio=auto` also carries `frame`: `frame.width` and
`frame.height` are the picture you sent, and `frame.content` is its box in `image.data`, as fractions.
The model generates at a fixed set of ratios, so your picture is placed on a canvas at the nearest one
and the box records where it sits. The clients crop to it; over plain HTTP, [The
frame](../../models/nano-banana.md#the-frame) is the four numbers and one crop. Naming another ratio
is a deliberate reframe: that answer carries no `frame` and says `reframed` in `warnings`.

A generation is `POST /models/<id>/generate`, with the prompt and the model's own parameters in the
query string and no body. The Nano Banana ids are called this way; `internvl-u` produces its picture
through `predict` with its `mode` parameter. An edit of a published image or of your own photograph is
the predict route with a `prompt`. A generation is never cached: the same prompt twice is two
pictures.

## Limits

| what | limit | over it |
|---|---|---|
| inferences in flight, per key | 3 | `429`, not a queue |
| uploads being received at once, the whole host | 8 | `429` with `Retry-After`, immediately |
| uploaded bytes in flight, the whole pool | up to 24 MB, following measured link capacity | `429` with `Retry-After`, after waiting up to 20 s |
| prediction JSON body | 4 KB | `413` |
| a backend response body | 16 MiB | discarded; `502` if no backend returns a valid answer |
| an uploaded image | 15 MB | `413` |
| an uploaded image, decoded | 50 megapixels | `400` |

The three are three across the whole team, not three per person: one key is one team's share of the
GPU pool. **Only inference counts** — the two predict routes and `generate`. A dataset listing, a
an image's detail and the model directory are answered from memory and queue behind no machine, so they
are never refused for being one request too many; a page that loads several of them at once is not
competing with anybody's inference.
