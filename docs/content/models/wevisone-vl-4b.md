# `wevisone-vl-4b`

WeVisOne-VL-4B — **text in, instances out**, from [Tencent's own code and
checkpoint](https://github.com/Tencent/WeVisOne-VL). Say what to find — `wheat head`, `leaf`, `a
person` — and every instance of it comes back as a box **and a mask of its own**, at your image's own
resolution.

It is open-vocabulary: nothing in the container carries a list of classes, and the phrase you type is
the class it reports. A frame with none of what you asked for answers an empty list rather than an
error.

**[Try it on the live demo](https://api.global-wheat.com/play?model=wevisone-vl-4b)** — the model is
already selected; type what to look for, or press one of the two example chips.

## Parameters

<!-- parameters:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | — | text, at most 120 characters | What to find, in words: a class name or a phrase, e.g. 'wheat head'. Every object in the answer is one instance of it, and a frame with none answers an empty list. |
| `max_instances` | `64` | `1` – `100` | How many instances to answer. It is the wait as much as the count: each one costs about half a second, and the model stops looking at this number. |
<!-- parameters:end -->

`prompt` is **required**: this model has no default class, and an empty phrase would ask for "something".
Tencent's own package calls it `query`; this contract calls it `prompt`, like every other model here
that takes text, so one script can point at two models by changing the id.
`max_instances` is the wait as much as the count — the model writes one six-token box per instance and
decodes a mask for each, so **sixty instances is about half a minute** and one is a few seconds. It
stops looking at that number rather than generating more and dropping the rest.

Nothing else is exposed: no threshold, no side, no class list. The picture's own resolution is what the
masks come back at, up to four megapixels — the pixel decoder produces one map per instance at your
size, so a twelve-megapixel photograph with forty heads is two gigabytes of masks inside the container.

## What comes back

One object per instance, in the same shape a dataset's annotations arrive in:

```json
{"model": "wevisone-vl-4b", "resolution_used": 1024,
 "objects": [{"idx": 0, "category": "wheat head",
              "bbox": [148.5, 809.0, 72.7, 47.1], "area": 2463.0,
              "rle": {"size": [1024, 1024], "counts": "…"}}],
 "computed_by": {"machine": "dgx-v100", "gpu": "Tesla V100-DGXS-32GB",
                 "compute_capability": "7.0", "dtype": "float16", "…": "…"}}
```

- **There is no `score`.** The model either names an object or answers `None` for a target it cannot
  find; it publishes no per-instance confidence, and one invented from a mask's area would be a fact
  about this service rather than about the picture.
- **`category` is your prompt**, because that is the class: two calls on one frame with different
  phrases are two different questions.
- **`rle` is COCO's compressed counts**, decoded by the same client code a dataset's geometry is.
  `area` is there so a decoder can be checked against one number.
- **`resolution_used` is the canvas the masks were decoded on** (1024), not the size you sent: the
  pixel decoder refines on a fixed square and the answer is upsampled to your image, which is what
  explains what a boundary can and cannot resolve.

## What it is good at

On a close-up frame with about forty heads, overlapping and awned, it found 43 instances and every
mask followed its own head — separate masks through a cluster, awns cut off, no merging into one blob.

**Its nearest neighbour here is [`sam3.1-meta`](sam3.1-meta.md), and the difference is where the pixels
come from.** SAM 3.1 runs the picture at a fixed 1008-pixel square and answers masks from its own
detector; this model keeps your image's own resolution (up to four megapixels), writes a box per
instance as text, and decodes each mask on a 1024 square before upsampling it to your size. It also
reports no score, so a caller accepts every instance or none rather than choosing a threshold.

Two calls on one frame with different phrases are two different answers — ask for `leaf` on the same
frame and the same machinery returns leaves.

## What it is not

- **Fast.** About 25 seconds for forty instances, proportional to how many it finds. It holds a lane
  for that long, which is why the declaration gives it 90 seconds rather than the default ten.
- **A detector with confidences.** No score means a caller cannot threshold an answer; the model's own
  judgement about what counts as an instance is the whole filter.
- **One phrase per call.** Two classes are two requests, not one.
- **Unbounded in instances.** A hundred per call is the ceiling, and past that it stops looking rather
  than answering partially.
- **Trained for botanists?** No — WeVisOne-VL is a general referring-segmentation model. It happens to
  recognise `wheat head` well, which is a measurement on one frame class, not a guarantee across
  growth stages, lighting or `gwhs` desk photographs.

## Served under

Apache-2.0, Tencent's code and weights. The model is
[`tencent/WeVisOne-VL-4B`](https://huggingface.co/tencent/WeVisOne-VL-4B); the inference code this
container vendors is [Tencent/WeVisOne-VL](https://github.com/Tencent/WeVisOne-VL).
