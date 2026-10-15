# Examples

Everything here is a snippet you can copy: no dependency beyond NumPy and Pillow, no
`pycocotools`, and no code of ours is needed to read what the API returns. Class names and the
mask palette always come from the dataset's own description, `GET /datasets/{dataset}`, rather
than from a figure or a blog post — fetch it once per session and keep it.

## Reading a semantic mask (`gwfss`)

A `gwfss` row's annotation *is* an image: `mask_url` points at a three-channel PNG where each
pixel's color names its class. The class description carries the palette, so the mapping is read,
never hard-coded except as the convenience constant in [`label_masks.py`](label_masks.py), which
transcribes the upstream `class.json` pinned in `datasets/sources.yaml`.

```python
import numpy as np
from PIL import Image
from label_masks import GWFSS_CLASSES, label_masks

description = requests.get(f"{API}/datasets/gwfss", headers=KEY).json()
palette = {c["name"]: c["color"] for c in description["classes"]}   # names come from here
mask = np.asarray(Image.open(mask_url))
for name, boolean in label_masks(mask, description["classes"]).items():
    print(name, boolean.sum())
```

`label_masks` asserts that no color outside the palette appears, so a mask the upstream never
declared fails loudly instead of being silently mislabeled. Background is one of the classes; a
head mask is `masks["head"]`, never `masks[1]`.

## Decoding an object's RLE (`gwhs`, `gwhs-temp`)

An instance dataset publishes geometry, not a mask image. The detail response for one row carries
each object's box, category, area, attributes, and its COCO run-length mask:

```json
{"idx": 0, "category": "spikelet_-_side1", "bbox": [231.0, 923.0, 126.0, 162.0],
 "area": 12074.0, "attributes": ["Green-Spikelet"],
 "rle": {"size": [2509, 592], "counts": "Zofa05S^2;E;E;F8K4L5K4L4M3L5L2O1N2O1N3M2M3…"}}
```

`rle.size` is `[height, width]` and `rle.counts` is the upstream's compressed string, verbatim.
Decoding is the twenty lines in [`rle_decode.py`](rle_decode.py) — and its
[JavaScript](rle_decode.js) and [Kotlin](rle_decode.kt) ports:

```python
from rle_decode import rle_decode

height, width = annotation["rle"]["size"]
flat = rle_decode(annotation["rle"]["counts"], (height, width))
mask = np.array(flat).reshape(height, width, order="F")   # COCO is column-major
```

Decode a row's objects, stack them, and `mask.any(axis=0)` gives the row's union; `flat.sum()`
equals the `area` the upstream published, which is the cheapest check that a decoder is right.
The API never decodes anything: it returns the string the annotation was published with, so the
bytes you get are the bytes the lab wrote.
