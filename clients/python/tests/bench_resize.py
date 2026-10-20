"""What resizing costs on the participant's machine, measured rather than assumed.

Run it explicitly, with the output visible::

    uv run pytest tests/bench_resize.py -s

It is not collected by a plain ``pytest`` run, and it commits no fixtures: a 12 MP JPEG and a 12 MP
PNG are generated here, because the point is the work this machine does on a photograph's worth of
pixels, not a file's bytes. The ceilings are deliberate overestimates — they catch a client that
spends seconds resizing, not a twenty-percent regression — and the numbers they measure belong in
the maintainers' notes, never in the participant-facing docs.
"""

from __future__ import annotations

import io
import statistics
import time

import numpy as np
from PIL import Image

import global_wheat

SIDE = 1008
"""The side ``sam3.1-meta`` declares, which is the largest target any client resizes to."""

JPEG_CEILING_MS = 400.0
"""A 12 MP JPEG goes through ``Image.draft``, so it is decoded at one quarter in each direction."""

PNG_CEILING_MS = 500.0
"""A PNG has no decoder shortcut: this is the full 12 MP decode and a full-size resize."""

PASSTHROUGH_CEILING_MS = 10.0
"""An image that already fits is read to its header and sent as it is."""

RUNS = 5


def photograph(width: int, height: int, fmt: str) -> bytes:
    """Build a large image with enough structure to decode like a photograph.

    Parameters
    ----------
    width, height : int
        The pixel size.
    fmt : str
        ``"JPEG"`` or ``"PNG"``.

    Returns
    -------
    bytes
        The encoded file.
    """
    columns, rows = np.meshgrid(
        np.linspace(0, 255, width, dtype=np.uint8), np.linspace(0, 255, height, dtype=np.uint8)
    )
    pixels = np.dstack([columns, rows, np.full_like(columns, 96)])
    encoded = io.BytesIO()
    Image.fromarray(pixels).save(encoded, format=fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return encoded.getvalue()


def median_ms(payload: bytes) -> tuple[float, bytes]:
    """Time the whole preparation, once to warm up and then the median of five.

    Parameters
    ----------
    payload : bytes
        The image to prepare.

    Returns
    -------
    tuple of (float, bytes)
        The median in milliseconds, and what the last run produced.
    """
    global_wheat._prepare_upload(payload, SIDE)  # one warm-up: page cache and Pillow's first decode
    timings: list[float] = []
    prepared = b""
    for _ in range(RUNS):
        started = time.perf_counter()
        prepared = global_wheat._prepare_upload(payload, SIDE)
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings), prepared


def test_resizing_a_photograph_is_cheap_enough_to_be_the_default() -> None:
    """One test, because the three figures only mean something beside each other."""
    jpeg = photograph(4000, 3000, "JPEG")
    png = photograph(4000, 3000, "PNG")
    small = photograph(1008, 756, "JPEG")

    jpeg_ms, jpeg_out = median_ms(jpeg)
    png_ms, png_out = median_ms(png)
    small_ms, small_out = median_ms(small)

    print()
    for label, source, prepared, taken in (
        ("4000x3000 JPEG ", jpeg, jpeg_out, jpeg_ms),
        ("4000x3000 PNG  ", png, png_out, png_ms),
        ("already 1008 px", small, small_out, small_ms),
    ):
        print(
            f"{label} {len(source) / 1024:6.1f} KB -> {len(prepared) / 1024:6.1f} KB"
            f" {taken:7.3f} ms"
        )

    assert jpeg_ms < JPEG_CEILING_MS, f"JPEG preparation took {jpeg_ms:.1f} ms"
    assert png_ms < PNG_CEILING_MS, f"PNG preparation took {png_ms:.1f} ms"
    assert small_ms < PASSTHROUGH_CEILING_MS, f"pass-through took {small_ms:.3f} ms"
    assert small_out is small, "an image that already fits must not be re-encoded"
    assert len(jpeg_out) < len(jpeg), "the resized file must be smaller than the photograph"
