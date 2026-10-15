"""Decode a COCO run-length encoded mask, in Python and without pycocotools.

Every GWHS object's geometry is stored as the compressed counts string COCO defines, and so is
every hosted SAM endpoint's mask output. The decoder is short enough to copy into a project, and
the JavaScript and Kotlin ports beside this file are the same algorithm.

The counts string is little-endian base 32: each character carries five bits, bit 0x20 continues
the number, and bit 0x10 on the last chunk sign-extends it. The first value is where the first
run of ones ends; every value from the fourth on is a delta against the value two positions
earlier. The runs alternate, background first.

COCO flattens a mask **column-major** (its Fortran order), so the flat result here indexes as
`row + column * height`. With NumPy that is `np.array(mask).reshape(height, width, order="F")`.
"""

from __future__ import annotations

from collections.abc import Sequence


def rle_decode(counts: str, size: Sequence[int]) -> list[bool]:
    """Decode a COCO compressed RLE into a flat boolean mask in COCO's column-major order.

    Parameters
    ----------
    counts : str
        The upstream's compressed counts string, used verbatim.
    size : sequence of int
        Mask size as `(height, width)`, exactly as `rle.size` publishes it.

    Returns
    -------
    list of bool
        `height * width` values in COCO's column-major order; True is the object.
    """
    runs: list[int] = []
    index = 0
    while index < len(counts):
        value = shift = 0
        while True:
            chunk = ord(counts[index]) - 48
            index += 1
            value |= (chunk & 0x1F) << shift
            shift += 5
            if not chunk & 0x20:
                if chunk & 0x10:
                    value |= -1 << shift
                break
        runs.append(value + (runs[-2] if len(runs) > 2 else 0))

    height, width = size
    mask = [False] * (height * width)
    position = 0
    for run, length in enumerate(runs):
        if run % 2:
            mask[position : position + length] = [True] * length
        position += length
    return mask
