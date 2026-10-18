# `sam3.1-meta`

SAM 3.1 — text-prompted segmentation, [Meta's own code and checkpoint](https://github.com/facebookresearch/sam3).
Send an image address (or a photograph) and a prompt; it answers with boxes, scores, and masks. The
id says whose weights these are: `meta` is the upstream release, not a fine-tune.

Expect a **conspicuous head in a close-up**, not a field count: on a 1024 px quadrat of small, dense
heads it returns nothing, at any threshold. That is this checkpoint rather than the API, and a fine-tune
served as an id of its own is where field counts would come from.

## Parameters

Every parameter a model takes is **declared by that model**, published by `GET /models`, and checked
against that declaration before the request reaches a machine. A name this model does not declare, a
value outside its declared bounds or set, or a name sent twice is a `400`, and the message lists what
the model does accept — so a typo costs one round trip, not a guess.

<!-- parameters:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | `wheat head` | text, at most 200 characters | The text this model is asked to find in the image. |
| `boxes` | — | `[[x, y, w, h, label], …]`, in the image's pixels | Exemplar boxes: the object you mean, shown rather than named. A negative box says what it is not. |
| `points` | — | `[[x, y, label], …]`, in the image's pixels | Click points: one spot on the object, or one that is not it. |
| `confidence_threshold` | `0.5` | `0` – `1` | How sure the model must be before it reports an object. |
| `imgsz` | `1008` | `672`, `784`, `896`, `1008`; a larger value runs at `1008` | The square side the network runs at. A smaller one is faster and worse on small objects; a larger one runs at the ceiling and says so in `warnings`. |
<!-- parameters:end -->

Every parameter is optional: leaving one out is the default in the table. Two of them are not simple
values — `boxes` and `points` are JSON, and `imgsz` has a ceiling — and both are described once, in
[Raw HTTP § Model parameters](../api/http.md#model-parameters).

## Prompting

**One prompt at a time is the easy case, and any of them works alone.** A phrase finds the concept; a
box is an exemplar of it; a point marks one spot. They combine because the detector's prompt is the
text features concatenated with the geometry features, so a box beside a phrase is an exemplar beside
a concept rather than a replacement for it:

```bash
# a phrase
"$API/models/sam3.1-meta/predict?prompt=wheat%20head"
# a phrase and one exemplar box — a box around a head you know is one
"$API/models/sam3.1-meta/predict?prompt=wheat%20head&boxes=[[480,290,110,360,1]]"
# a box alone, no text
"$API/models/sam3.1-meta/predict?boxes=[[480,290,110,360]]"
```

What was measured on a 512 px `gwfss` row, `prompt=wheat head&confidence_threshold=0.25`: text alone
found 18 objects; one exemplar box found 13, overlapping the text answer by IoU 0.98, and adding a
**negative** box took 16 down to 13 — the label subtracts. A point is accepted and runs, but its
effect is not characterised: a positive point beside the phrase changed the answer in a way that no
longer matched the text-only result, and a negative point did not reliably subtract. Boxes are what
this page shows for that reason. If you use points, read the answer rather than trusting it.

## Size

`imgsz` is one of four sides this model was actually run at — `672`, `784`, `896`, `1008` — and 1008
is both the default and the ceiling. A larger value is not an error: it runs at the ceiling and says
so in `warnings`, and `resolution_used` carries the side that ran either way. Build queues are
counted in requests, not pixels, so a smaller side is the one lever that makes a loop faster:

| `imgsz` | one 512 px row | against the 1008 answer |
|---|---|---|
| `672` | 0.13 s | largest object IoU 0.97 |
| `896` | 0.18 s | 0.97 |
| `1008` | 0.21 s | — |

Faster and cheaper, and **worse on small objects**: the price of a smaller side is that a head a few
tens of pixels across stops resolving, which is already this checkpoint's weak case. Measured on a
RTX 3090 with the container's own image; your numbers will differ by the card.

## The answer

Both routes answer the same body. One object of a real answer — a 5184×3456 photograph, run at 1008 —
with `counts` truncated, because it is one long string:

```json
{ "model": "sam3.1-meta", "resolution_used": 1008,
  "computed_by": { "machine": "lab-3090", "gpu": "NVIDIA GeForce RTX 3090",
                   "compute_capability": "8.6", "cuda": "12.8", "torch": "2.10.0+cu128" },
  "objects": [
    { "idx": 0, "category": "wheat head", "score": 0.582, "area": 259818.0,
      "bbox": [2321.0, 1876.7, 766.1, 766.7],
      "rle": { "size": [3456, 5184], "counts": "Zofa05S…" } } ] }
```

## Reading it

- **`bbox` is `[x, y, width, height]`** in the image's own pixels — the same convention the dataset
  surface uses, so one drawing routine reads both.
- **`rle` is COCO's compressed mask**, the same format `gwhs` publishes, and decoding it is
  [the client's](../api/clients.md) job — it also keeps the raw `rle` beside the mask so the decoded
  pixel count can be checked against `area`.
- **`area` is the decoded mask's pixel count, and `bbox` is the model's own box.** They are separate
  predictions and do not agree exactly: slack between a mask and the box around it is this contract
  working as declared, not a mistake.
- **`computed_by` names the machine that produced the answer** — the operator's label for it, the card,
  the compute capability, the CUDA and torch versions. A mask's boundary is the output of kernels the
  runtime chooses, so two machines can disagree by a pixel, by a percent of a mask's area, or by one
  object at the margin; when they do, this is the field to read. It is a reproducibility record, not an
  inventory: no address, no memory figure, no path.
- **`resolution_used` is the side the model actually ran at.** A mask computed at 1008 and mapped back
  onto a 2509-pixel-tall photograph is upscaled about 2.5×, and its boundary is visibly coarse at full
  size.

## Try it

[The live demo](../examples/demo.md) runs this model from this site with your key, and shows one
recorded answer as a figure you can check yours against. [The Jupyter
quickstart](../examples/quickstart.md) calls it from Python in three lines.
