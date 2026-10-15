"""Turn a GWFSS mask image into one boolean array per class.

GWFSS ships a semantic mask: one image per row, three RGB channels, every pixel one of four
colors. The palette below is the upstream's own, published next to the dataset in
`GlobalWheat/GWFSS_Segformer_b0@485865aa6c67ec78a2400e3dfd888d8847186c49#class.json` and pinned
in `datasets/sources.yaml`. A paper figure's colors are a rendering and are not this palette.

The function is generic over palettes; the constant is the GWFSS one. Hand it a decoded image and
index the result by class name:

    masks = label_masks(np.asarray(Image.open("gwfss/masks/00000000.png")))
    heads = masks["head"]          # a boolean array of the same height and width
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

GWFSS_CLASSES: tuple[dict[str, Any], ...] = (
    {"id": 0, "name": "background", "color": (0, 0, 0)},
    {"id": 1, "name": "head", "color": (50, 255, 132)},
    {"id": 2, "name": "stem", "color": (50, 132, 255)},
    {"id": 3, "name": "leaf", "color": (214, 255, 50)},
)
"""The upstream's four classes, exactly as its published `class.json` lists them."""


def label_masks(
    image: np.ndarray,
    classes: Sequence[Mapping[str, Any]] = GWFSS_CLASSES,
) -> dict[str, np.ndarray]:
    """Split a palette mask into one boolean array per class.

    Parameters
    ----------
    image : numpy.ndarray
        Mask image as `(height, width, 3)`, as Pillow decodes it.
    classes : sequence of mapping, optional
        Palette entries with `name` and `color` keys. Defaults to the GWFSS palette.

    Returns
    -------
    dict of str to numpy.ndarray
        One `(height, width)` boolean array per class, in palette order.

    Raises
    ------
    ValueError
        A pixel holds a color outside the palette. Publishing or reading a mask the upstream
        never declared is a source-data question, and ignoring the pixel silently would hide it.
    """
    result: dict[str, np.ndarray] = {}
    known = np.zeros(image.shape[:2], dtype=bool)
    for entry in classes:
        color = np.asarray(entry["color"], dtype=image.dtype)
        mask = np.all(image[..., :3] == color, axis=-1)
        result[str(entry["name"])] = mask
        known |= mask
    if not known.all():
        raise ValueError("mask holds a color that is not in the palette")
    return result
