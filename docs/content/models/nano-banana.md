# Nano Banana

<!-- models: nano-banana-2 nano-banana-2.1 nano-banana-pro -->

Three image models from Google, served through **Vertex AI**: a picture from a prompt, or a new
version of a picture you already have. The weights are Google's and run on Google's hardware;
nothing is downloaded to you, and there is nothing to set up.

The three ids are one product line at three tiers. **`nano-banana-2.1`** is the cheaper default and
the one to reach for; **`nano-banana-2`** is the earlier checkpoint, served for comparison; and
**`nano-banana-pro`** is the largest and most expensive. One page because the contract, the output
and the analysis are shared; the table below is the difference that matters.

**[Try the 2.1](https://api.global-wheat.com/play?model=nano-banana-2.1)** — it is selected already,
and the row's photograph is what the prompt changes. The `results` eye shows the picture the model
produced.

*Served through Google Vertex AI, subject to the [Google Cloud Platform
terms](https://cloud.google.com/terms) and the [Generative AI Prohibited Use
Policy](https://policies.google.com/terms/generative-ai). The weights are not distributed by this
service.*

## The three ids

| | `nano-banana-2.1` | `nano-banana-2` | `nano-banana-pro` |
|---|---|---|---|
| offers | 1K, 2K | 512, 1K, 2K | 1K |
| one 1K picture, list price | $0.0336 | $0.0672 | $0.134 |
| team allowance, 1K pictures | 200 | 100 | 50 |

The prices are the upstream list prices at 1K. The team allowance is counted in **credits**, where
one 1K 2.1 picture is one: a 2K picture costs 1.5, one on `nano-banana-2` costs 2, and one on
`nano-banana-pro` costs 4. The allowance is for the whole event.

## Parameters

All three answer the same three parameters, and they ride in the **query string**: the request is
one URL, with no body and no image to address.

### `nano-banana-2.1`

<!-- parameters:nano-banana-2.1:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | — | text, at most 300 characters | What to draw, or how to change the picture you sent. Required: this model has no default prompt. |
| `aspect_ratio` | `1:1` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | The shape of the frame the model produces. |
| `image_size` | `1K` | `1K`, `2K` | The longer side of the picture. The list is what this deployment offers, not the upstream model's full range: 4K is left out because its answer can exceed the gateway's payload ceiling. |
<!-- parameters:nano-banana-2.1:end -->

### `nano-banana-2`

<!-- parameters:nano-banana-2:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | — | text, at most 300 characters | What to draw, or how to change the picture you sent. Required: this model has no default prompt. |
| `aspect_ratio` | `1:1` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | The shape of the frame the model produces. |
| `image_size` | `1K` | `512`, `1K`, `2K` | The longer side of the picture. The list is what this deployment offers, not the upstream model's full range: 4K is left out because its answer can exceed the gateway's payload ceiling. |
<!-- parameters:nano-banana-2:end -->

### `nano-banana-pro`

<!-- parameters:nano-banana-pro:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | — | text, at most 300 characters | What to draw, or how to change the picture you sent. Required: this model has no default prompt. |
| `aspect_ratio` | `1:1` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | The shape of the frame the model produces. |
| `image_size` | `1K` | `1K` | The longer side of the picture. The list is what this deployment offers, not the upstream model's full range: 4K is left out because its answer can exceed the gateway's payload ceiling. |
<!-- parameters:nano-banana-pro:end -->

`prompt` is **required** in every call: this model has no default prompt. It is also the one value
whose length is worth knowing before you write it: the query is capped at 2 KB, which is about 200
Chinese characters or 600 Latin ones once percent-encoded, and a longer prompt is a `400` rather
than a truncated instruction.

`4K` is not offered. Its base64 body can exceed the gateway's 16 MiB answer ceiling, and an answer
that cannot be relayed is worse than a size that is not promised.

## What comes back

```json
{
  "model": "nano-banana-2.1",
  "objects": [],
  "answer": "Here is a photorealistic shot of a single wheat spike against dark soil.",
  "image": {
    "media_type": "image/png",
    "width": 1376,
    "height": 768,
    "data": "iVBORw0KGgoAAAANSUhEUg…"
  },
  "resolution_used": 1376,
  "computed_by": {"machine": "vertex-global"}
}
```

`objects` is present and empty, so the same client parses this answer and a segmenter's. The prose,
where the model wrote any, is in `answer`; the picture is in `image`. `image.data` is base64 of the
encoded file, exactly as the model produced it — the picture is not stored and served from a URL,
it is the answer.

The clients decode it for you: `image["picture"]` is a Pillow image in Python, the file's bytes in
JavaScript and Java. Nothing on this service re-encodes it.

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

`generate` is the one call with no image of your own. It is never cached: the same prompt twice is
two pictures and two charges.

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

`predict_file` sends your photograph; `predict` does the same on a published row. The client
reduces the photograph to the model's reference side before sending it, and an edit is re-run at the
model's own output size — `resolution_used` reports what it actually produced.

## Measured

From the host in Frankfurt, through the public API, one picture per call:

| call | latency | what it produced |
|---|---|---|
| `generate`, 1K, 1:1 | **15.2 s** | 1.5 MB PNG, 1024 × 1024 |
| `generate`, 1K, 16:9 | **18.7 s** | 1.9 MB PNG, 1376 × 768 |
| `predict`, 1K, `gwhd` row 0 | **17.2 s** | 1.6 MB PNG, 1024 × 1024 |

The answer carries the model's reasoning as `thought` parts, including a draft picture the same size
as the final one; this service drops both, so the body is one picture rather than two. 2K and
`nano-banana-pro` are measured before the event, not extrapolated.

## Limits and cost

One call is one picture. It is charged against **your team's allowance** — 200 credits, where a 1K
2.1 picture is one — and against a shared pot with a daily ceiling. When either is exhausted the API
answers `429` with the reason in the message. Waiting until Japan midnight resets the daily figure,
not the event allowance.

The service is shared: at most **two** pictures are generated at once across all teams, and a third
request is refused rather than queued. Retry in a few seconds; a `429` that says `at once` is
capacity, and one that names credits or the budget is the allowance.

## What it is and is not for

**It makes pictures, not measurements.** A count, a mask or a diagnosis it gives you is a
description of an image it drew. Use `tasselnetv4`, `sam3.1-meta` or `gwfss-segformer-b4` for
numbers and geometry.

**It edits at the level of the frame.** "Colder light", "a botanical illustration of this", "the
same scene at dawn" return a plausible new frame. One awn, one lesion or one label is beyond what
one prompt and one generation will reliably give you.
