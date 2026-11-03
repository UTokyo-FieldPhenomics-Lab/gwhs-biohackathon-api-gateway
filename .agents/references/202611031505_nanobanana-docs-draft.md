# Draft: the `nano-banana` model page (not yet published)

This is the page the docs will carry once the backend is running and `GET /models` has been recorded.
It is a draft on purpose: the parameter tables must be generated from the recorded fixture, not from
this file. The measured table is filled from the host after deployment.

---

# Nano Banana

Three image models from Google, served through **Vertex AI**: the model makes a picture from a
prompt, or changes a picture you give it. The weights are Google's and run on Google's hardware;
nothing is downloaded to you, and there is nothing to set up.

| id | what it is | sizes |
|---|---|---|
| `nano-banana-2.1` | Google's cheaper image model — the default recommendation | 1K, 2K |
| `nano-banana-2` | the earlier checkpoint, served for comparison | 512, 1K, 2K |
| `nano-banana-pro` | the largest and most expensive | 1K |

*Served through Google Vertex AI, subject to
[Google's Gemini API terms](https://ai.google.dev/gemini-api/terms). The weights are not distributed
by this service.*

## Three ways to call it

- **`generate`** — a picture from a prompt, with no image of your own.
- **`predict_file`** — send your own photograph and a prompt describing the change.
- **`predict`** — the same, on a published dataset row.

The answer is one `ModelPrediction` like every other model's: `objects` is empty, the picture is in
`image`, and the prose (where the model wrote any) is in `answer`.

## Parameters

| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | — | text, at most 300 characters | What to draw, or how to change the picture you sent. Required. |
| `aspect_ratio` | `1:1` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | The shape of the frame. |
| `image_size` | `1K` | `1K`, `2K` (`nano-banana-2` also `512`; `pro` only `1K`) | The longer side of the picture. 4K is not offered: its answer can exceed the API's payload ceiling. |

The parameters ride in the query string, so the whole request is one URL. The query is capped at
2 KB, which is about 200 Chinese characters or 600 Latin ones once percent-encoded; a 300-character
Latin prompt fits, a 300-character Chinese one may not.

## What comes back

```json
{
  "model": "nano-banana-2.1",
  "objects": [],
  "answer": "Here is a photorealistic shot of a single wheat spike against dark soil.",
  "image": {
    "media_type": "image/png",
    "width": 1024,
    "height": 1024,
    "data": "iVBORw0KGgoAAAANSUhEUg…"
  },
  "resolution_used": 1024,
  "computed_by": {"machine": "vertex-global"}
}
```

`image.data` is base64 of the PNG, exactly as the model produced it. The picture is not stored or
served from a URL: it is the answer, in the answer.

## Generate

```sh
curl -s -H "X-API-Key: $KEY" \
  "https://api.global-wheat.com/models/nano-banana-2.1/generate?prompt=a%20wheat%20field%20at%20golden%20hour&aspect_ratio=16:9&image_size=1K" \
  -o answer.json

python - <<'PY'
import base64, json
body = json.load(open("answer.json"))
base64.b64decode(body["image"]["data"])
PY
```

```python
from global_wheat import Client

client = Client()                       # key from GLOBAL_WHEAT_API_KEY
answer = client.generate(
    "nano-banana-2.1",
    prompt="a wheat field at golden hour",
    aspect_ratio="16:9",
    image_size="1K",
)
answer["image"]["picture"].save("field.png")   # already decoded by the client
```

## Edit a picture

```python
answer = client.predict_file(
    "nano-banana-2.1",
    "my-photo.jpg",
    prompt="the same crop under colder evening light",
    image_size="1K",
)
answer["image"]["picture"].save("evening.png")
```

The client reduces the photograph to the model's reference side before sending it. An edit is
re-run at the model's own output size, and `resolution_used` reports it.

## Measured

| call | latency | what it produced |
|---|---|---|
| `generate`, 1K, 1:1 | **15.2 s** | 1.5 MB PNG, 1024 × 1024 |
| `generate`, 1K, 16:9 | **15.2 s** | 2.0 MB PNG, 1376 × 768 |

2K and `nano-banana-pro` are measured on the host before this page is published.

## Limits and cost

One call is one picture. A picture is charged against a **team allowance** — 200 pictures of 1K
`nano-banana-2.1` for the event, where a 2K picture counts 1.5 and one on `nano-banana-pro` counts
4 — and against a shared pot with a daily ceiling. When either is exhausted the API answers `429`
with the reason in the message; waiting until Japan midnight resets the daily figure, not the event
allowance.

The service is shared: at most two pictures are generated at once across all teams, and a third
request is refused rather than queued. Retry in a few seconds.

## What it is and is not for

**It makes pictures, not measurements.** A count, a mask or a diagnosis it gives you is a
description of an image it drew. Use `tasselnetv4`, `sam3.1-meta` or `gwfss-segformer-b4` for
numbers and geometry.

**It edits at the level of the frame.** "Colder light", "a botanical illustration of this", "the
same scene at dawn" return a plausible new frame. One awn, one lesion or one label is beyond what
one prompt and one generation will reliably give you.
