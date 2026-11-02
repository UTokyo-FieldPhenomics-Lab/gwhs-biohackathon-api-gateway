# `dinov3-vith16plus`

The same container and the same contract as [`dinov3-vitl16`](dinov3-vitl16.md), with Meta's
**ViT-H+/16** checkpoint instead of the L:
[the LVD-1689M weights](https://huggingface.co/facebook/dinov3-vith16plus-pretrain-lvd1689m),
pretrained on web images. What the output **is**, how to decode it and what can be done with it are
the L page's, unchanged; this page only lists what is different.

| | `dinov3-vitl16` | `dinov3-vith16plus` |
|---|---|---|
| hidden size (`features.dim`) | 1024 | **1280** |
| layers / heads | 24 / 16 | 32 / 20 |
| default `side` | 512 | **384** |
| maximum `side` | 768 | **640** |
| resident, bfloat16 | 0.6 GiB | **1.6 GiB** |
| one inference, 4:6 frame at `side=512` | 27.2 ms | **44.8 ms** |
| answer at the default, square | 2.8 MB | **2.0 MB** |
| answer at the ceiling, square | 6.3 MB | **5.5 MB** |

The wider vectors are the whole difference: they cost more memory and more wire bytes at the same
grid, which is why this id's ceiling is 640 rather than 768. At 640 a square frame is 40 × 40 × 1280,
about 4.1 MB of float16 before base64 — inside the same answer budget as the L's 768.

**Do not compare the two.** A 1024-dimensional vector and a 1280-dimensional one are different
spaces from different weights; there is no alignment between them in this API.

## Parameters

<!-- parameters:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `side` | `384` | `224` – `640`; a larger value runs at `640` | Longer side of the frame the network runs at, in pixels; the shorter side follows the image's aspect ratio. Larger sides give a finer patch grid and a larger answer. |
<!-- parameters:end -->

`side` is the longer side of the frame, the shorter side follows the aspect ratio, and each dimension
is floored to 16. The default is 384 and the maximum 640; a larger value is answered at 640 with
`resolution_clamped` in `warnings`.

## What comes back

```json
{"model": "dinov3-vith16plus", "objects": [], "resolution_used": 384,
 "embedding": [0.04127, -0.31882, "…"],
 "features": {"size": [24, 24], "dim": 1280, "dtype": "float16", "data": "AAAA…"}}
```

`size` is the frame divided by 16, `dim` is 1280, and `data` is decoded exactly as the L page says.
Every client attaches `features.grid`, a float32 array of `rows × columns × 1280`.

## Measured

On `lab-3090`, bfloat16, one image per call:

| | |
|---|---|
| load | 0.5–0.7 s |
| one inference, 4:6 frame at `side=384` | 26.2 ms |
| one inference, 4:6 frame at `side=512` | 44.8 ms |
| peak torch memory | 2191 MiB |
| answer at `side=384`, square | 1.5 MB of float16, 2.0 MB as JSON |
| answer at `side=640`, square | 4.1 MB of float16, 5.5 MB as JSON |

The peak is dominated by the weights and the CUDA context; the forward pass at these sides adds little.

## See also

- [`dinov3-vitl16`](dinov3-vitl16.md) — the output, the decoders and the analysis recipes
- [Clients](../api/clients.md) — `features.grid` in all three languages
- [Raw HTTP § Backbone results](../api/http.md#backbone-results)
