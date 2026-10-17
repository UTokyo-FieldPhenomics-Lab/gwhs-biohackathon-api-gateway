"""The decoders: a round trip through the counts string, and the palette split."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from rle_fixture import encode

from global_wheat import label_masks, rle_decode

EXAMPLE = Path(__file__).resolve().parents[3] / "docs/content/examples/example-prediction.json"
"""A recorded call: eighteen objects of a 512x512 prediction, each with its area and its RLE."""

CLASSES = [
    {"id": 0, "name": "background", "color": "#000000"},
    {"id": 1, "name": "head", "color": "#32ff84"},
    {"id": 2, "name": "stem", "color": "#3284ff"},
]


def test_round_trip_on_a_mask_with_the_awkward_cases() -> None:
    """Every pixel position, including a first pixel that is set and a last that is not."""
    height, width = 7, 5
    flat = np.zeros(height * width, dtype=bool)
    flat[[0, 1, 2, 8, 9, 30, 34]] = True
    mask = rle_decode(encode(flat), (height, width))
    assert mask.shape == (height, width)
    assert np.array_equal(mask.reshape(-1, order="F"), flat)


def test_a_recorded_answer_decodes_to_its_own_area() -> None:
    """The published example is the fixture: each mask's pixel count is the ``area`` beside it."""
    prediction = json.loads(EXAMPLE.read_text(encoding="utf-8"))["response"]
    for obj in prediction["objects"]:
        mask = rle_decode(obj["rle"]["counts"], obj["rle"]["size"])
        assert int(mask.sum()) == int(obj["area"])


def test_counts_that_do_not_fit_their_size_are_refused() -> None:
    """A truncated string is a wrong question, not an empty mask."""
    with pytest.raises(ValueError, match="describe"):
        rle_decode(encode([True, False, True, False]), (3, 3))


def test_label_masks_splits_by_palette() -> None:
    """One boolean array per class, in palette order, from one pass over the pixels."""
    pixels = np.zeros((2, 3, 3), dtype=np.uint8)
    pixels[0, 0] = (0x32, 0xFF, 0x84)  # head
    pixels[1, 2] = (0x32, 0x84, 0xFF)  # stem
    masks = label_masks(pixels, CLASSES)
    assert masks["head"].sum() == 1 and masks["head"][0, 0]
    assert masks["stem"].sum() == 1 and masks["stem"][1, 2]
    assert masks["background"].sum() == 4


def test_label_masks_accepts_rgba() -> None:
    """A PNG may decode as RGBA, and the alpha channel is not part of the palette."""
    pixels = np.zeros((1, 1, 4), dtype=np.uint8)
    pixels[0, 0] = (0x32, 0xFF, 0x84, 255)
    assert label_masks(pixels, CLASSES)["head"][0, 0]


def test_a_colour_outside_the_palette_is_refused() -> None:
    """Silently dropping the pixel would hide a mask from a dataset that is not this one."""
    pixels = np.zeros((1, 1, 3), dtype=np.uint8)
    pixels[0, 0] = (1, 2, 3)
    with pytest.raises(ValueError, match="not in the palette"):
        label_masks(pixels, CLASSES)
