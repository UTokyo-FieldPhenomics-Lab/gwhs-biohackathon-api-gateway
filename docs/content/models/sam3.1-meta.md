# `sam3.1-meta`

SAM 3.1 — text-prompted segmentation, [Meta's own code and checkpoint](https://github.com/facebookresearch/sam3).
Send an image address (or a photograph) and a prompt; it answers with boxes, scores, and masks. The
id says whose weights these are: `meta` is the upstream release, not a fine-tune.

## Parameters

Every parameter a model takes is **declared by that model**, repeated by `GET /models`, and forwarded
verbatim. This one declares two:

| parameter | default | range | what it does |
|---|---|---|---|
| `prompt` | `wheat head` | any text | what the model is asked to find |
| `confidence_threshold` | `0.5` | `0` – `1` | how sure it must be before it reports an object |

Both are optional: a parameter you leave out is the published default. Anything outside a declared
range is a `400`, and the message names the parameter. There is deliberately no resolution parameter —
the side the model runs at belongs to the machine that holds the weights, and it is 1008.

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

## What it does, measured

Prompted with `wheat head`, on the published rows, at 1008:

| image | result |
|---|---|
| `gwhs-temp` row 0 — 592×2509, one spike on dark ground | **1 object**, score 0.520, `area` 173,777, box 254×1461 — the whole head |
| `gwhs-temp` row 1 | 1 object, score 0.715 |
| `gwhs-temp` rows 2–3 | 0 objects |
| `gwhd` row 0 — 1024×1024 field quadrat, **144 annotated heads** | **0 objects**, at every threshold from 0.5 down to 0.05 |
| `gwfss` row 0 | 0 objects — and correct: that row's own palette holds no head pixels |
| `gwfss` row 4 — 512×512, cut heads lying on straw | **18 objects** at `confidence_threshold` 0.25, `Σ mask` = `Σ area` = 109,419 px |

Read together: at 1008 this model segments **a conspicuous head in a close-up** and finds **none of the
small, dense heads in a field crop**. A head in `gwhd` is about 57 px across in a 1024 px image — near
1:1 at 1008 — so the field case is probably not a resolution problem, and asking for a larger side may
not change it.

What it answers is a frame whose heads are separated and large: one spike filling a portrait frame, or
cut heads on straw. What it does not answer is a field quadrat, whichever threshold you send.

## Try it

[The live demo](../examples/demo.md) runs this model from this site with your key, and replays one
recorded answer beside it. [The Jupyter quickstart](../examples/quickstart.md) calls it from Python in three lines.
