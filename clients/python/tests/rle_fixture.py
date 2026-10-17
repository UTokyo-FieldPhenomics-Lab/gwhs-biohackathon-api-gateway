"""The shared helper the geometry tests build their fixtures with.

COCO's compressed RLE is written here as well as read, so a test can hand the decoder a mask it
chose rather than one it found: a round trip is then a claim about the algorithm, not about a
recorded string that might have been edited.
"""

from __future__ import annotations

from collections.abc import Sequence


def encode(flat: Sequence[bool]) -> str:
    """Compress one column-major mask into COCO's counts string.

    Parameters
    ----------
    flat : sequence of bool
        The mask flattened column-major, as COCO defines the order.

    Returns
    -------
    str
        The compressed counts string.
    """
    runs: list[int] = []
    last = False
    count = 0
    for value in flat:
        if value == last:
            count += 1
        else:
            runs.append(count)
            count = 1
            last = value
    runs.append(count)

    out: list[str] = []
    for index, run in enumerate(runs):
        value = run - (runs[index - 2] if index > 2 else 0)
        more = True
        while more:
            chunk = value & 0x1F
            value >>= 5
            more = value != -1 if chunk & 0x10 else value != 0
            if more:
                chunk |= 0x20
            out.append(chr(chunk + 48))
    return "".join(out)
