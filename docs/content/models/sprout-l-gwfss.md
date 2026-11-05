# `sprout-l-gwfss`

SPROUT-L is the laboratory's own foundation model for plant phenotyping: a 361 M parameter diffusion
model pretrained on 2.6 million unlabelled open-field photographs. This id is that model with a task
head fine-tuned on the [GWFSS](https://doi.org/10.1016/j.plaphe.2025.100084) training split. Send it a
wheat canopy and it answers the three foreground organ classes in the dataset's own palette — `head`,
`stem`, `leaf` — one object per class present, COCO RLE. Background is the complement and is not
returned.

## At a glance

| field | value |
|---|---|
| Task | semantic organ segmentation |
| Input | a dataset image or upload |
| Output | one object per foreground class, with a COCO RLE `mask` |
| Runs at | 512 px square |
| Typical wait | 0.29–0.32 s |
| License | [Apache-2.0](#references) |

![The model's classes on image 4, beside the mask the dataset publishes for it](sprout-row4-prediction.jpg)

**[Try it on the live demo](https://api.global-wheat.com/play?model=sprout-l-gwfss)** — the model is
already selected; pick `gwfss` image 4 (`row` 4) and press Run.

## When to use it

**Good at**

- In-field wheat canopy photographs, square frames, a camera positioned over the crop — the GWFSS
  capture.
- `gwfss` images, where it is measured below, and `gwhd` images, which are the same kind of capture.
- Three organ masks at 512 px, in the dataset's own palette.

**Not for**

- **A desk photograph of one ear.** `gwhs` is out of domain twice over: it photographs one ear on a
  table rather than a canopy in a field, and it appears nowhere in the training data.
- **Instances or a count.** One object per class present, not one per head; counting is a
  connected-component pass over the mask.
- **A guaranteed score.** The ten published images are not a held-out set; the author's own evaluation is
  quoted separately below.

**Limits**

- The side is fixed at 512, square, **without preserving the aspect ratio**. For a `gwfss` or `gwhd`
  image that resize is exact; anything else is stretched.
- `gwhs` images are portrait frames — 247 to 1601 pixels wide and 547 to 4263 tall — so they are both
  unfamiliar and distorted. A single ear on a bench is out of domain, not a hard case that better
  prompting fixes.
- The model declares no parameters: any query parameter is a `400` rather than being ignored.

## Call it

With the client from [Clients](../api/clients/index.md) set up, or the `API` and `KEY` variables from [Raw HTTP](../api/http/index.md#five-calls):

=== "Python"

    ```python
    answer = client.predict("sprout-l-gwfss", "gwfss", 4)
    ```

=== "JavaScript"

    ```js
    const answer = await client.predict("sprout-l-gwfss", "gwfss", 4);
    ```

=== "Java"

    ```java
    Map<String, Object> answer = client.predict("sprout-l-gwfss", "gwfss", 4);
    ```

=== "HTTP"

    ```bash
    curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
      -d '{"dataset": "gwfss", "row": 4}' \
      "$API/models/sprout-l-gwfss/predict"
    ```

## Parameters

<!-- parameters:start -->
This model declares **no parameters**: an image is the whole request, and a query string is refused rather than ignored.
<!-- parameters:end -->

The network runs once per 256 × 256 window, four windows at stride 256, at a fixed timestep — the
author's validated protocol. There is no parameter to change the side, the window, or the scale.

## What comes back

One object per class that has pixels. Background is the complement and is not returned.

```json
{"model": "sprout-l-gwfss", "resolution_used": 512,
 "objects": [
   {"idx": 0, "category": "head", "bbox": [0, 0, 512, 512], "score": 0.902, "area": 86911.0,
    "rle": {"size": [512, 512], "counts": "…"}},
   {"idx": 1, "category": "stem", "bbox": [0, 0, 512, 512], "score": 0.790, "area": 16145.0,
    "rle": {"size": [512, 512], "counts": "…"}},
   {"idx": 2, "category": "leaf", "bbox": [0, 0, 512, 511], "score": 0.739, "area": 95280.0,
    "rle": {"size": [512, 512], "counts": "…"}}]}
```

Two fields are easy to read as more than they are:

- **`bbox` is the class's extent, not an object's box.** Heads scattered across the frame are one
  `head` object whose box is the whole frame. Counting them is a connected-component pass over the
  mask, not a count of objects.
- **`score` is the mean probability of that class over the pixels it owns** — derived, uncalibrated,
  and not comparable between classes.

## Performance

### Quality

Ten published `gwfss` images, this container, a laboratory 3090:

| class | IoU |
|---|---|
| head | 0.885 |
| leaf | 0.832 |
| background | 0.761 |
| stem | 0.432 |
| **mIoU** | **0.7275** |
| pixel accuracy | 0.8905 |

Measured by this container's own `tests/bench.py`, which runs its `infer` and `outputs` over the ten
images against the masks those images publish, and is reproducible on any machine with the checkpoint.

**This is not a generalisation figure.** The ten images are not a held-out set; the fine-tune's own split
is recorded (884 train, 99 validation, 110 test) but which of those contains the ten images this API
publishes is not. The author's own evaluation is **78.80 % mIoU on validation and 75.10 % on test**,
over her 99 and 110 images. Those are her numbers on her split, quoted as hers.

### Speed

One inference takes **287 ms** on the ten images, and **318 ms** through `POST /predict` including the
image fetch.

## References

- **Paper** — Xiang et al., “SPROUT: A Scalable Diffusion Foundation Model for Agricultural Vision,”
  arXiv 2026. [arXiv:2603.27519](https://arxiv.org/abs/2603.27519)
- **Code** — [UTokyo-FieldPhenomics-Lab/SPROUT](https://github.com/UTokyo-FieldPhenomics-Lab/SPROUT)
- **Weights** — pretrained SPROUT checkpoints are linked from the repository; the fine-tune head served
  here was delivered by the model's author.
- **License** — [Apache-2.0](https://github.com/UTokyo-FieldPhenomics-Lab/SPROUT/blob/main/LICENSE)
