# `dinov3-vitl16`

DINOv3 ViT-L/16 — a **backbone**, not a task model, from [Meta's DINOv3
release](https://github.com/facebookresearch/dinov3). The checkpoint is the **LVD-1689M** one,
pretrained on web images ([model
card](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)). The `sat493m` satellite
variant is a different checkpoint, trained on overhead imagery, and is not served.

It has no class list, no box and no mask. What it answers is its own tokens: one **CLS** vector for
the whole picture and one vector per **16 × 16 patch**. A head that turns those numbers into a mask,
a count or a class is yours to fit — this service publishes the tokens, not a head's opinion.

**[Try it on the live demo](https://api.global-wheat.com/play?model=dinov3-vitl16)** — the
`features` eye draws the first three principal components of the patch grid, and the `image` eye
turns the photograph off so the field is read on its own.

![Row 4 of gwfss: the photograph, and the first three principal components of its 32 × 32 patch
grid](dinov3-row4-features.jpg)

The two panels are one `side=512` call. The right one is not a segmentation: it is the patch grid
projected onto its own three leading directions, one direction per colour channel. Heads, stems and
background separate because the features separate — no class was assigned to any of them, and the
colours mean nothing on another picture.

## Parameters

<!-- parameters:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `side` | `512` | `224` – `768`; a larger value runs at `768` | Longer side of the frame the network runs at, in pixels; the shorter side follows the image's aspect ratio. Larger sides give a finer patch grid and a larger answer. |
<!-- parameters:end -->

`side` is the **longer side of the frame**, not a square: the shorter side follows the image's aspect
ratio, and each dimension is floored to a multiple of 16. A 4:6 photograph at `side=512` runs at
336 × 512, so its grid is 21 × 32, not 32 × 32. The maximum is 768; a larger value is answered at
768 with `resolution_clamped` in `warnings`. The ceiling is the answer's size, not the card: at 768 a
square frame is 48 × 48 × 1024, about 4.7 MB of float16 before base64.

## What comes back

```json
{"model": "dinov3-vitl16", "objects": [], "resolution_used": 512,
 "embedding": [-1.10202, 0.27331, "…"],
 "features": {"size": [32, 32], "dim": 1024, "dtype": "float16", "data": "AAAA…"}}
```

- **`objects` is present and empty**, as a counting answer's is. A backbone cannot answer geometry,
  and a body that omitted the key would make a client branch on the model rather than read it.
- **`embedding` is the CLS token**: 1024 floats for this id, the whole-image descriptor the model was
  trained to make. It is not a class score and not a probability; it is rounded to five decimals on
  the wire.
- **`features` is the patch grid.** `size` is `[rows, columns]` of the **frame the network ran at**,
  not of your image; `dim` is 1024 for this id and 1280 for the H+; `dtype` is `float16`; `data` is
  base64 of the grid, row-major and channels-last, little-endian. The raw `data` stays beside a
  decoded `grid` in every client, so the encoding is checkable.
- **`resolution_used` is the longer side actually run**, after the request was floored to a multiple
  of 16. Asking for 500 on a square image answers `496`.

The published recipe is three calls; the clients do it for you and attach `features.grid`:

```python
import base64
import numpy as np

grid = np.frombuffer(base64.b64decode(answer["features"]["data"]), dtype="<f2")
grid = grid.reshape(*answer["features"]["size"], answer["features"]["dim"]).astype(np.float32)
```

The vectors are **not unit length**. Compare them with cosine, not with a raw dot product; the
lengths vary with how much texture a patch holds and are not a confidence.

## What the output is

The encoder's **last hidden state**, and nothing else:

- The token sequence is `[CLS]`, then the checkpoint's four **register** tokens, then one token per
  patch in row-major order. The container drops the registers: they exist to absorb global
  information and are not part of an answer. `embedding` is token 0; `features` is the patch tokens
  that follow them.
- **The last layer is read.** DINOv3's dense features are strong at the output layer, which is the
  difference from its predecessors; no intermediate layer is selected or exposed.
- **The patch is 16 pixels.** The frame is the whole image resized so its longer side is `side`, each
  dimension floored to a multiple of 16. The grid covers the entire picture: `grid[r, c]` is the
  patch at row `r`, column `c`, and `grid[0, 0]` is the picture's top-left.
- **The container runs in bfloat16 and publishes float16.** The weights are distributed as float32;
  nothing in these features needs the wider format, and half precision is what makes the answer 2 MB
  instead of 4.
- There is **no softmax, no class score, no box and no mask** anywhere in this path. The CLS is a
  representation, not a label; a logistic regression on it is a head you trained, not something the
  encoder answered.

## What you can do with it

These are recipes, not API routes. The API returns the tokens; the analysis is yours.

### Find what looks like a patch

Normalise the vectors, pick one patch or the mean of a region, and take the cosine to every patch.
On `gwfss` row 4, pointing at one head lights the other heads and leaves the straw dark:

```python
rows, columns = answer["features"]["size"]
flat = answer["features"]["grid"].reshape(-1, answer["features"]["dim"])
unit = flat / np.linalg.norm(flat, axis=1, keepdims=True)

reference = unit[12 * columns + 20]                 # row 12, column 20
similarity = (unit @ reference).reshape(rows, columns)
```

For a region, average the **unit** vectors of the patches inside it and renormalise:

```python
patch = answer["features"]["grid"][8:16, 4:12].reshape(-1, answer["features"]["dim"])
patch = patch / np.linalg.norm(patch, axis=1, keepdims=True)
reference = patch.mean(axis=0)
reference = reference / np.linalg.norm(reference)
```

A threshold on that map is a segmenter you chose; a colormap is a picture. Neither is computed here.

### k-nearest-neighbour segmentation

Label a handful of patches — by hand, or from a dataset's mask — and give every other patch the
majority label of its nearest labelled patches in feature space. This is the cheapest way to turn the
grid into a mask, and it needs no training. The grid is coarse: one cell is 16 pixels of the frame,
so a boundary is resolved to 16 pixels unless you interpolate.

### A linear probe or a fine-tune

Fit a small head on the patch tokens (dense prediction) or on the CLS (image-level classification).
Freeze the backbone first: it is the part that costs a card, and the head is usually what a small
labelled set can afford to train. The checkpoint is the LVD-1689M one; the paper's downstream
numbers are the reference, not a measurement of this service.

### Retrieve or cluster pictures

The CLS vectors are comparable **within one model**: cosine between two pictures' `embedding` is a
similarity, and k-means or UMAP over a corpus is a map of it. Two ids do not mix — 1024 and 1280
dimensions are different spaces, not two views of one.

### Compare two pictures

Two aligned pictures give two grids of the same shape. A per-cell cosine is a change map; a global
average is a similarity. This is the same arithmetic as the patch query, with the reference being
the other picture's cell.

## What it is not

- **Not a segmenter.** No class, no box, no mask, no per-object score. Use
  [`gwfss-segformer-b4`](gwfss-segformer-b4.md) for organs or
  [`sam3.1-meta`](sam3.1-meta.md) for text-prompted instances.
- **Not a detector, and not a counter.** A head is required; the encoder does not place anything.
- **Not comparable across the two backbones.** `dinov3-vitl16` and `dinov3-vith16plus` publish vectors
  of different widths from different weights; a cosine between them is meaningless.
- **Not normalised.** Use cosine; a dot product mixes similarity with vector length.
- **Not the satellite checkpoint.** `facebook/dinov3-vitl16-pretrain-sat493m` is a different model
  trained on overhead imagery; it is not served, and its features are not these.

## Measured

On `lab-3090`, bfloat16, one image per call:

| | |
|---|---|
| load | 0.5–0.8 s |
| one inference, 512 × 512 frame | 38.6 ms |
| one inference, 4:6 frame at `side=512` | 27.2 ms |
| peak torch memory | 604–641 MiB |
| answer at `side=512`, square | 2.1 MB of float16, 2.8 MB as JSON |
| answer at `side=768`, square | 4.7 MB of float16, 6.3 MB as JSON |

The answer's size is the reason the ceiling exists; the card is not the constraint at these widths.

## See also

- [`dinov3-vith16plus`](dinov3-vith16plus.md) — the same contract at 1280 dimensions
- [Clients](../api/clients.md) — `features.grid`, already decoded, in Python, JavaScript and Java
- [Raw HTTP § Backbone results](../api/http.md#backbone-results) — the fields and their encodings
