"""Tests for the pinned class vocabulary: what a palette is, and what a mask may paint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from biohack_datasets.labels import PaletteError, UnknownMaskColour, load_palette

CLASS_JSON = [
    {"id": 0, "name": "background", "color": [0, 0, 0]},
    {"id": 1, "name": "head", "color": [50, 255, 132]},
    {"id": 2, "name": "stem", "color": [50, 132, 255]},
    {"id": 3, "name": "leaf", "color": [214, 255, 50]},
]


def _write(tmp_path: Path, document: object = CLASS_JSON) -> Path:
    """Write a class.json into `tmp_path` and return its path."""
    path = tmp_path / "class.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_palette_is_read_verbatim(tmp_path: Path) -> None:
    """The classes are the artifact's, in its order, with each colour as #rrggbb."""
    palette = load_palette(_write(tmp_path))

    assert palette.classes == (
        (0, "background", "#000000"),
        (1, "head", "#32ff84"),
        (2, "stem", "#3284ff"),
        (3, "leaf", "#d6ff32"),
    )


def test_a_mask_reports_the_classes_it_paints(tmp_path: Path) -> None:
    """Presence comes from the mask's own pixels, in class-id order."""
    palette = load_palette(_write(tmp_path))
    image = Image.new("RGB", (2, 2), (0, 0, 0))
    image.putpixel((0, 0), (50, 255, 132))
    image.putpixel((1, 1), (214, 255, 50))

    assert palette.class_ids_in(image) == (1, 3)


def test_the_background_is_not_reported_as_presence(tmp_path: Path) -> None:
    """Every mask paints background, so recording it would put every row in the class facet."""
    palette = load_palette(_write(tmp_path))
    image = Image.new("RGB", (2, 2), (0, 0, 0))
    image.putpixel((0, 0), (50, 255, 132))

    assert palette.class_ids_in(image) == (1,)
    assert palette.background_id == 0


def test_a_colour_outside_the_palette_is_refused(tmp_path: Path) -> None:
    """A mask that paints an undeclared colour would publish a legend that lies."""
    palette = load_palette(_write(tmp_path))
    image = Image.new("RGB", (1, 1), (1, 2, 3))

    with pytest.raises(UnknownMaskColour, match=r"\(1, 2, 3\)"):
        palette.class_ids_in(image)


def test_a_mask_with_more_colours_than_the_palette_is_refused(tmp_path: Path) -> None:
    """A photographic mask is rejected without materialising a table the size of its pixels."""
    palette = load_palette(_write(tmp_path))
    image = Image.new("RGB", (6, 1))
    for x in range(6):
        image.putpixel((x, 0), (x, x, x))

    with pytest.raises(UnknownMaskColour, match="more colours"):
        palette.class_ids_in(image)


@pytest.mark.parametrize(
    "document",
    [
        [],
        {},
        [{"id": 0, "name": "a"}],
        [{"id": 0, "name": "a", "color": [0, 0]}],
        [
            {"id": 0, "name": "a", "color": [0, 0, 0]},
            {"id": 0, "name": "b", "color": [1, 1, 1]},
        ],
    ],
)
def test_a_vocabulary_that_cannot_be_read_is_refused(tmp_path: Path, document: object) -> None:
    """Every refusal names the path; none of them guesses a palette."""
    with pytest.raises(PaletteError, match="class.json"):
        load_palette(_write(tmp_path, document))


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    """A viewer page downloaded by mistake fails here, and says which file it was."""
    path = tmp_path / "class.json"
    path.write_text("<!doctype html>", encoding="utf-8")

    with pytest.raises(PaletteError, match="class.json"):
        load_palette(path)
