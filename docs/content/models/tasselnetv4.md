# `tasselnetv4`

TasselNetV4 — **counting**, not segmentation, from [the paper's own code and
checkpoint](https://arxiv.org/abs/2509.20857) (ISPRS J. Photogrammetry and Remote Sensing, 2025).
Show it a few examples of the thing you want counted and it answers **a number**, with the local-count
field that number is the sum of.

It is class-agnostic: nothing in the container knows what a wheat head looks like. The request carries
**exemplar boxes**, the model matches their appearance across the picture, and three of them is the
paper's setting. If you have no examples to show, no other model here will do it either — this is the
only counting model on the service.

**[Try it on the live demo](https://api.global-wheat.com/play?model=tasselnetv4)** — the model is
already selected; draw two or three boxes around heads on the picture and press Run. The `image` eye
turns the photograph off so the field is read on its own.

## Parameters

<!-- parameters:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `boxes` | — | `[[x, y, w, h, label], …]`, in the image's pixels | Exemplar boxes: one object of the kind to count, drawn. The model matches their appearance across the image, and three is the paper's setting. |
<!-- parameters:end -->

`boxes` is **required**: without an example the model has nothing to match, and the request is a
`400`. The label must be `1` — extract-and-match has no negative exemplar, so a `0` would be silently
ignored and is refused instead. Nothing else is exposed: no threshold, no point count, no resolution.
The prompt is geometry in the image's own pixels; see
[Raw HTTP § Geometry](../api/http.md#geometry-boxes-and-points).

An exemplar can be larger than the finest window the model counts in — a spike that fills the frame.
That is not refused: the model counts at its coarsest window and the answer says so with
`{"code": "exemplar_clamped"}` in `warnings`.

## What comes back

A count, and the field it was summed from. `objects` is present and empty, so one client parses this
answer and a segmenter's with the same code.

```json
{"model": "tasselnetv4", "resolution_used": 384,
 "objects": [],
 "count": 21.7,
 "density": {"size": [23, 23], "values": [0.041, 0.0, 1.284, "…"]}}
```

- **`count` is the answer.** It is a real number rather than a rounded one, because it is an integral
  of a field in which one cell can hold a fraction of an object.
- **`density` is that field**, on the model's own grid: `size` is `[rows, columns]`, `values` are the
  cells' local counts row by row, and they add up to `count`. A cell is a counting window, not a
  pixel — 17, 21 or 23 cells fill the 384-pixel-tall frame, depending on which of the model's three
  levels your exemplar's size selects, so a cell is 45–60 pixels of a 1024-pixel-tall photograph.
  Draw it as a heatmap; `size` travels with the values because the grid is not always square.
- **There are no points and no instances.** The field says how much mass sits in a window; it does not
  say where one object ends and the next begins. See [what it is not](#what-it-is-not).

## What it is good at

TasselNetV4 is trained on PAC-105 and evaluated on **PAC-Somalia**, a set of plant species it has
never seen: the paper reports **MAE 8.9** at three exemplars (10.98 at one). Through this container,
on the official test images and their official exemplar boxes:

| what | measured here | paper |
|---|---|---|
| `salaamuko`, all 48 images, official 3 exemplars | MAE **8.75** | 8.88 (3-shot) |
| 32 species, one image each, official 3 exemplars | MAE **10.8** | 8.88 (3-shot) |

So it is at its own level, not better. **The spread across species matters more than the mean**: on
`salaamuko` many images are annotated at 46–48 and answered at 58–66, about **+15%**, while other
species land within a few. A GWHD close-up of dense, small heads is further out still — that is a
different scene from the ones it was trained on, and it over-counts there.

## What it is not

- **Not a detector.** No box, no mask, no point, no per-object score. If you need to know *which* head
  is where, use [`gwfss-segformer-b4`](gwfss-segformer-b4.md) (semantic organ masks) or
  [`sam3.1-meta`](sam3.1-meta.md) (text-prompted instance masks) — both answer geometry.
- **Not calibrated across pictures.** Two calls on two photographs give counts on one scale, but a
  systematic offset on one species does not cancel on another.
- **Not a region counter.** There is no box or polygon parameter: it counts what the exemplars match
  over the whole picture.

## Size

| what | measured on an RTX 3090 |
|---|---|
| the loaded process on the device | 790 MiB |
| one request, torch's allocator peak | 428 MiB |
| the same on a 49-megapixel image | 428 MiB |
| through the API, from the gateway host | 0.31 s per inference |

The peak does not follow the picture because the frame is resized to 384 px tall and tiled, one tile
at a time. Two facts follow for a participant: the model runs at **384 px tall** whatever you send
(`resolution_used`), and one container takes one request at a time — the queue is the gateway's, not
yours.

## Where it comes from

`akaxiao/TasselNetV4` on Hugging Face, Apache-2.0, at the revision that last touched
`tasselnetv4.pth`; the container fetches those bytes at build time and asserts their count. The
vendored code is upstream's tree at a recorded commit, unchanged. The field, the crop and the answer's
shape are this repository's — the model is not.
