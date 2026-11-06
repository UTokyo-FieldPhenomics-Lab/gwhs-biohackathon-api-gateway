# DINOv3 backbones

<!-- models: dinov3-vitl16 dinov3-vith16plus -->

**Built with DINOv3.** These models are served under the [DINOv3
License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/), which requires this
notice when the models are made available to a third party.

DINOv3 ViT-L/16 and ViT-H+/16 — the same encoder at two sizes, from [Meta's DINOv3
release](https://github.com/facebookresearch/dinov3). Both checkpoints are the **LVD-1689M** ones,
pretrained on web images; the `sat493m` satellite variant is a different checkpoint and is not served.
It has no class list, no box and no mask: it answers its own tokens — one **CLS** vector for the whole
picture and one vector per **16 × 16 patch** — and a head that turns those numbers into a mask, a count
or a class is yours to fit.

## At a glance

| field | value |
|---|---|
| Task | visual backbone; dense patch features and a whole-image descriptor |
| Input | a dataset image or upload, plus `side` |
| Output | `embedding` (CLS) and `features` (patch grid) |
| Runs at | L 224–768 px, default 512; H+ 224–640 px, default 384 |
| Typical wait | 17.7–44.8 ms |
| License | [DINOv3 License](#references) |

### The two ids

| property | `dinov3-vitl16` | `dinov3-vith16plus` |
|---|---|---|
| hidden size (`features.dim`) | 1024 | 1280 |
| default `side` | 512 | 384 |
| maximum `side` | 768 | 640 |
| answer at the default, square | 2.8 MB | 2.0 MB |
| answer at the ceiling, square | 6.3 MB | 5.5 MB |

**[Try the L](https://api.global-wheat.com/play?model=dinov3-vitl16)** ·
**[try the H+](https://api.global-wheat.com/play?model=dinov3-vith16plus)** — the `results` eye draws
the first three principal components of the patch grid, and the `image` eye turns the photograph off
so the field is read on its own.

## When to use it

**Good at**

- Dense features for segmentation, matching or retrieval without training a backbone.
- One descriptor per picture, for clustering, similarity or a linear probe.
- Finding what looks like a patch or a region: point at one head and the other heads light up.
- Comparing two aligned pictures cell by cell.

**Not for**

- **A segmenter, detector or counter.** There is no class, box, mask or object count. Use
  [`gwfss-segformer-b4`](gwfss-segformer-b4.md) for organs or [`sam3.1-meta`](sam3.1-meta.md) for
  text-prompted instances.
- **Comparing the two ids.** 1024 and 1280 dimensions are different spaces from different weights; a
  cosine between them is meaningless.
- **A raw dot product.** The vectors are not unit length; use cosine.
- **The satellite checkpoint.** `facebook/dinov3-vitl16-pretrain-sat493m` is a different model trained
  on overhead imagery; its features are not these.

**Limits**

- `side` is the longer side of the frame; the shorter side follows the aspect ratio, and each
  dimension is floored to a multiple of 16. A 4:6 photograph at `side=512` runs at 336 × 512, so its
  grid is 21 × 32, not 32 × 32.
- A value above the id's maximum is answered at the maximum with `resolution_clamped`.
- The answer grows with the side: the largest are about 4–5 MB of float16 before base64.

## Call it

With the client from [Clients](../api/clients/index.md) set up, or the `API` and `KEY` variables from [Raw HTTP](../api/http/index.md#five-calls):

=== "Python"

    ```python
    answer = client.predict("dinov3-vitl16", "gwfss", 0, side=512)
    ```

=== "JavaScript"

    ```js
    const answer = await client.predict("dinov3-vitl16", "gwfss", 0, {side: 512});
    ```

=== "Java"

    ```java
    Map<String, Object> answer = client.predict(
        "dinov3-vitl16", "gwfss", 0, Map.of("side", 512));
    ```

=== "HTTP"

    ```bash
    curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
      -d '{"dataset": "gwfss", "row": 0}' \
      "$API/models/dinov3-vitl16/predict?side=512"
    ```

The H+ takes the same call with `dinov3-vith16plus`; its default side is 384.

## Parameters

### `dinov3-vitl16`

<!-- parameters:dinov3-vitl16:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `side` | `512` | `224` – `768`; a larger value runs at `768` | Longer side of the frame the network runs at, in pixels; the shorter side follows the image's aspect ratio. Larger sides give a finer patch grid and a larger answer. |
<!-- parameters:dinov3-vitl16:end -->

### `dinov3-vith16plus`

<!-- parameters:dinov3-vith16plus:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `side` | `384` | `224` – `640`; a larger value runs at `640` | Longer side of the frame the network runs at, in pixels; the shorter side follows the image's aspect ratio. Larger sides give a finer patch grid and a larger answer. |
<!-- parameters:dinov3-vith16plus:end -->

`side` is the **longer side of the frame**, not a square: the shorter side follows the image's aspect
ratio, and each dimension is floored to a multiple of 16. A 4:6 photograph at `side=512` runs at
336 × 512, so its grid is 21 × 32, not 32 × 32. A larger value than the id's maximum is answered at the
maximum with `resolution_clamped` in `warnings`. Asking for 500 on a square image answers `496`,
because the frame is floored to a multiple of 16 as well.

## What comes back

```json
{"model": "dinov3-vitl16", "objects": [], "resolution_used": 512,
 "embedding": [-1.10202, 0.27331, "…"],
 "features": {"size": [32, 32], "dim": 1024, "dtype": "float16", "data": "AAAA…"}}
```

- **`objects` is present and empty**, as a counting answer's is. A backbone cannot answer geometry,
  and a body that omitted the key would make a client branch on the model rather than read it.
- **`embedding` is the CLS token**: 1024 floats on the L and 1280 on the H+, the whole-image
  descriptor the model was trained to make. It is not a class score and not a probability; it is
  rounded to five decimals on the wire.
- **`features` is the patch grid.** `size` is `[rows, columns]` of the **frame the network ran at**,
  not of your image; `dim` is the id's width; `dtype` is `float16`; `data` is base64 of the grid,
  row-major and channels-last, little-endian. The raw `data` stays beside a decoded `grid` in every
  client, so the encoding is checkable.
- **`resolution_used` is the longer side actually run**, after the request was floored to a multiple
  of 16.

### What the output is

The encoder's **last hidden state**, and nothing else. The same for either id:

- The token sequence is `[CLS]`, then the checkpoint's four **register** tokens, then one token per
  patch in row-major order. The container drops the registers: they exist to absorb global information
  and are not part of an answer. `embedding` is token 0; `features` is the patch tokens that follow.
- **The last layer is read.** DINOv3's dense features are strong at the output layer; no intermediate
  layer is selected or exposed.
- **The patch is 16 pixels.** The frame is the whole image resized so its longer side is `side`, each
  dimension floored to a multiple of 16. `grid[r, c]` is the patch at row `r`, column `c`, and
  `grid[0, 0]` is the picture's top-left.
- **The container runs in bfloat16 and publishes float16.** The weights are float32; half precision is
  what keeps an answer at a few megabytes instead of twice that.
- There is **no softmax, no class score, no box and no mask** anywhere in this path.

The figure below is one `side=512` call from the L. The right panel is the patch grid projected onto
its own three leading directions, one direction per colour channel:

![Row 4 of gwfss: the photograph, and the first three principal components of its 32 × 32 patch
grid](../static/images/models/dinov3-row4-features.jpg)

Heads, stems and background separate because the features separate — no class was assigned to any of
them, and the colours mean nothing on another picture. The H+ returns the same grid shape at a given
side, with 1280 values per cell.


## What you can do with it

These are recipes, not API routes. The API returns the tokens; the analysis is yours.

### Find what looks like a patch

Normalise the vectors, pick one patch or the mean of a region, and take the cosine to every patch. On
`gwfss` image 4, pointing at one head lights the other heads and leaves the straw dark:

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
grid into a mask, and it needs no training. The grid is coarse: one cell is 16 pixels of the frame, so
a boundary is resolved to 16 pixels unless you interpolate.

### A linear probe or a fine-tune

Fit a small head on the patch tokens (dense prediction) or on the CLS (image-level classification).
Freeze the backbone first: it is the part that costs a card, and the head is usually what a small
labelled set can afford to train. The checkpoint is the LVD-1689M one; the paper's downstream numbers
are the reference, not a measurement of this service.

### Retrieve or cluster pictures

The CLS vectors are comparable **within one id**: cosine between two pictures' `embedding` is a
similarity, and k-means or UMAP over a corpus is a map of it. The two ids do not mix — 1024 and 1280
dimensions are different spaces, not two views of one.

### Compare two pictures

Two aligned pictures give two grids of the same shape. A per-cell cosine is a change map; a global
average is a similarity. This is the same arithmetic as the patch query, with the reference being the
other picture's cell.

## Performance

### Speed

Measured on `lab-3090`, bfloat16, one image per call:

| metric | `dinov3-vitl16` | `dinov3-vith16plus` |
|---|---|---|
| one inference, 4:6 frame at 384 | 17.7 ms | 26.2 ms |
| one inference, 4:6 frame at 512 | 27.2 ms | 44.8 ms |
| answer at the default, square | 2.8 MB | 2.0 MB |
| answer at the ceiling, square | 6.3 MB | 5.5 MB |

The answer grows with the side; the ceilings keep the largest at about 4–5 MB of float16 before
base64.

## References

- **Paper** — Siméoni et al., “DINOv3,” arXiv 2025. [arXiv:2508.10104](https://arxiv.org/abs/2508.10104)
- **Code** — [facebookresearch/dinov3](https://github.com/facebookresearch/dinov3)
- **Weights**
    - `dinov3-vitl16` — [facebook/dinov3-vitl16-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)
    - `dinov3-vith16plus` — [facebook/dinov3-vith16plus-pretrain-lvd1689m](https://huggingface.co/facebook/dinov3-vith16plus-pretrain-lvd1689m)
- **License** — [DINOv3 License](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md)
