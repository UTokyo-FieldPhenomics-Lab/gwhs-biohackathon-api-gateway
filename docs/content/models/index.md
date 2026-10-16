# Models

One model answers today, and calling it is one request: **you send an image address (or your own
photograph) and a prompt, and you get boxes, scores, and masks back**. No weights travel to you, and
nothing runs on your machine.

| id | what it is | runs at | measured |
|---|---|---|---|
| `sam3.1-meta` | SAM 3.1 — text-prompted segmentation, [Meta's own code and checkpoint](https://github.com/facebookresearch/sam3) | 1008 px square | 0.36 s per inference on a 3090; 3.7 GB resident + 1.8 GB per request |

An id names **whose weights these are**, not where they run: a collaborator's fine-tune of the same
model appears as an id of its own, named after the data it was trained on. Every id this API serves is
something it answers — a model with no machine behind it answers `0` and a call is refused rather than
queued.

`GET /models` is the only place an id is published, and the only place a model's parameters are:

```bash
curl -s -H "X-API-Key: $KEY" "$API/models"
```

```json
{ "models": [
  { "id": "sam3.1-meta", "resolution": 1008, "backends_up": 1, "max_in_flight": 1,
    "parameters": [
      { "name": "prompt", "default": "wheat head",
        "description": "The text this model is asked to find in the image." },
      { "name": "confidence_threshold", "default": 0.5, "minimum": 0.0, "maximum": 1.0,
        "description": "How sure the model must be before it reports an object." } ] } ] }
```

This page has no key and is built from markdown, so the map above is written by hand from what the
host answered on 16 October 2026 — the `curl` is what makes it live. `backends_up` is how many machines
are answering for that id right now; `0` means no machine is, and a call would be refused rather than
made to wait.

## Call it

```bash
# one published row: the gateway resolves the address, so you never send a URL
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"dataset": "gwhs-temp", "row": 0}' \
  "$API/models/sam3.1-meta/predict?prompt=wheat%20head&confidence_threshold=0.5"

# your own photograph: the file itself, as the multipart field `file`
curl -s -H "X-API-Key: $KEY" -F "file=@photo.jpg" \
  "$API/models/sam3.1-meta/predict/upload?prompt=wheat%20head"
```

Both routes answer the same body. One object of a real answer — a 5184×3456 photograph, run at 1008 —
with `counts` truncated, because it is one long string:

```json
{ "model": "sam3.1-meta", "resolution_used": 1008,
  "objects": [
    { "idx": 0, "category": "wheat head", "score": 0.582, "area": 259818.0,
      "bbox": [2321.0, 1876.7, 766.1, 766.7],
      "rle": { "size": [3456, 5184], "counts": "Zofa05S…" } } ] }
```

- **`bbox` is `[x, y, width, height]`** in the image's own pixels — the same convention the dataset
  surface uses, so one decoder reads both.
- **`rle` is COCO's compressed mask**, the same format `gwhs` publishes, and decoding it is
  [Post-processing](../post-processing/index.md)'s job — including the check that the decoded pixel
  count equals `area`, which is what tells you your decoder is right.
- **`area` is the decoded mask's pixel count, and `bbox` is the model's own box.** The two are
  separate predictions and do not agree exactly: slack between a mask and the box around it is this
  contract working as declared, not a mistake.
- **`resolution_used` is the side the model actually ran at.** The image is resized to a square
  *without preserving aspect ratio* beforehand, so a mask computed at 1008 and mapped back onto a
  2509-pixel-tall photograph is upscaled about 2.5×, and its boundary is visibly coarse at full size.
  That is the resolution, not the model.

The same two calls from a browser, on a page that runs on the API's own origin:

```js
const API = "https://api.global-wheat.com";
const headers = {"X-API-Key": KEY};

// a published row
const response = await fetch(`${API}/models/sam3.1-meta/predict?prompt=wheat%20head`, {
  method: "POST",
  headers: {...headers, "Content-Type": "application/json"},
  body: JSON.stringify({dataset: "gwhs-temp", row: 0}),
});
const answer = await response.json();

// your own photograph: the same request, with the file as the body
const form = new FormData();
form.append("file", file);
await fetch(`${API}/models/sam3.1-meta/predict/upload?prompt=wheat%20head`, {
  method: "POST", headers, body: form,
});
```

A refusal — an unknown model, a row that does not exist, a parameter the model will not accept — is
this API's one error body, `{"error": {"code": …, "message": …}}`. A `400` is the request's fault and
its `message` is the model's own words about what it refused; `503` means no machine is answering;
`504` means the model took too long and the backend was given up on.

## What it accepts

Every parameter a model takes is **declared by that model**, repeated by `GET /models`, and forwarded
verbatim. So the model above declares two today, and this is all there is to them:

| parameter | default | range | what it does |
|---|---|---|---|
| `prompt` | `wheat head` | any text | what the model is asked to find |
| `confidence_threshold` | `0.5` | `0` – `1` | how sure it must be before it reports an object |

Both are optional: a parameter you leave out is the model's published default. Anything outside a
declared range is a `400`, and the message names the parameter.

There is deliberately **no resolution parameter**. The side the model runs at belongs to the machine
that holds the weights: a client-set one would make the memory a single request needs a variable, and
the pool could no longer say in advance what fits on a card.

Because the declaration is the model's, nothing on this site needs changing when a model gains a
parameter or a second model appears — the page you are about to use builds its controls from
`GET /models` at the moment you pick a model.

## Run one yourself

<div id="play-frame">
  <iframe
    data-api-frame
    src="https://api.global-wheat.com/play"
    title="Global Wheat API — run one model"
    loading="lazy"
    style="display: block; width: 100%; height: 90vh; border: 0"
  ></iframe>
</div>

The page above is served by the API itself and framed here, so it is **same-origin with the API**: no
cross-origin call is involved anywhere, and your key is typed into the API's own pages — this site
never sees it. The reference and this page remember **one** key between them, in your browser on the
API's origin: set it on either and the other uses it, and Forget on either clears it for both. Paste
your token, pick a row or a photograph, and press Run; the image is drawn as soon as the address is
complete — whole, at whatever size the page can show it in — with each box labelled by the score that
model gave it, and the mask decoded in your browser by the same decoder this site hands you. The page
also prints the call it would make as a `curl` command, with your key left as `$KEY`, so anything you
set up here can be taken straight to a shell.

## A recorded call, decoded here

<p id="demo-state" role="status">Loading the recorded call…</p>

<figure id="demo-figure" hidden>
  <figcaption id="demo-check"></figcaption>
  <canvas id="demo-canvas"></canvas>
</figure>

<style>
  /* Scoped to this page's panel. The image is shown at the column's width, in a box of its own: a
     2509-pixel-tall row would otherwise be several screenfuls of page with the object somewhere in
     the middle of it. The script scrolls the box to the object once it has drawn one. */
  #demo-figure {
    max-height: 28rem;
    overflow-y: auto;
    margin: 1rem 0;
    border: 1px solid var(--md-default-fg-color--lightest);
    border-radius: 0.2rem;
  }
  #demo-canvas {
    display: block;
    width: 100%;
    height: auto;
  }
  #demo-figure figcaption {
    position: sticky;
    top: 0;
    padding: 0.4rem 0.6rem;
    background: var(--md-default-bg-color);
    border-bottom: 1px solid var(--md-default-fg-color--lightest);
    color: var(--md-default-fg-color--light);
    font-size: 0.8rem;
  }
  #demo-state { color: var(--md-default-fg-color--light); font-size: 0.8rem; }
  #demo-check[data-equal="no"] { color: var(--md-typeset-del-color); }
</style>

<script src="../post-processing/rle_decode.js"></script>
<script src="demo.js"></script>

## What this model does, measured

Prompted with `wheat head`, on the published rows, at 1008:

| image | result |
|---|---|
| `gwhs-temp` row 0 — 592×2509, one spike on dark ground | **1 object**, score 0.520, `area` 173,777, box 254×1461 — the whole head |
| `gwhs-temp` row 1 | 1 object, score 0.715 |
| `gwhs-temp` rows 2–3 | 0 objects |
| `gwhd` row 0 — 1024×1024 field quadrat, **144 annotated heads** | **0 objects**, at every threshold from 0.5 down to 0.05 |
| `gwfss` row 0 | 0 objects — and correct: that row's own palette holds no head pixels |

Read together: at 1008 this model segments **a conspicuous head in a close-up** and finds **none of the
small, dense heads in a field crop**. A head in `gwhd` is about 57 px across in a 1024 px image — near
1:1 at 1008 — so the field case is probably not a resolution problem, and asking for a larger side may
not change it. Whether it would is untested.

What that means for a prompt: an object that fills a good part of the frame is answered well; a row of
field quadrats is not, whichever threshold you send.

## Next

- [Post-processing](../post-processing/index.md) — turning `rle.counts` into a mask, in three languages
- [Interactive API reference](../interactive.md) — every route, with a try-it button
