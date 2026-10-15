"""Tests for the GWFSS adapter, against a synthetic parquet with the measured schema.

The upstream publishes two structs and nothing else: no width, no height, and no annotation column.
The mask is an image whose colours are the classes, so the palette is what turns pixels into a
legend, and the tests paint masks out of it.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PIL import Image

from biohack_datasets.adapters import MissingVocabulary, get_adapter
from biohack_datasets.adapters.common import AdapterError
from biohack_datasets.adapters.gwfss import GwfssAdapter
from biohack_datasets.formats import UnknownImageFormat
from biohack_datasets.labels import Palette

BACKGROUND = (0, 0, 0)
HEAD = (50, 255, 132)
STEM = (50, 132, 255)
UNDECLARED = (1, 2, 3)

PALETTE = Palette(
    classes=((0, "background", "#000000"), (1, "head", "#32ff84"), (2, "stem", "#3284ff")),
)


def _png(image: Image.Image) -> bytes:
    """Return an image's PNG bytes."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _image(size: tuple[int, int] = (4, 3)) -> bytes:
    """Return a synthetic photo's bytes."""
    return _png(Image.new("RGB", size, (10, 20, 30)))


def _mask(colours: tuple[tuple[int, int, int], ...], size: tuple[int, int] = (4, 3)) -> bytes:
    """Return a mask that paints each colour on its own pixel, over a background."""
    image = Image.new("RGB", size, BACKGROUND)
    for x, colour in enumerate(colours):
        image.putpixel((x, 0), colour)
    return _png(image)


def _schema() -> pa.Schema:
    """Return the synthetic upstream's Arrow schema, as the pinned file declares it."""
    return pa.schema(
        [
            ("image", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            ("mask", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
        ]
    )


def _record(body: bytes, mask: bytes) -> dict[str, object]:
    """Return one synthetic parquet record."""
    return {
        "image": {"bytes": body, "path": "a.png"},
        "mask": {"bytes": mask, "path": "a_mask.png"},
    }


def _write(path: Path, records: list[dict[str, object]]) -> Path:
    """Write records as one parquet file and return its path."""
    pq.write_table(pa.Table.from_pylist(records, schema=_schema()), path)
    return path


def test_a_semantic_row_carries_its_mask_and_its_classes(tmp_path: Path) -> None:
    """The mask is copied verbatim and its classes come from its own pixels."""
    mask = _mask((HEAD, STEM))
    path = _write(tmp_path / "labelled-00000-of-00002.parquet", [_record(_image(), mask)])

    (row,) = list(GwfssAdapter(PALETTE).iter_rows([path]))

    assert row.row == 0
    assert row.split == "labelled"
    assert row.image_ext == "png"
    assert row.mask_ext == "png"
    assert row.mask == mask
    assert (row.width, row.height) == (4, 3)
    assert row.mask_classes == (1, 2)
    assert row.objects == () and row.tags == ()


def test_a_row_that_paints_only_background_carries_no_classes(tmp_path: Path) -> None:
    """The background is the absence of an annotation, not one."""
    path = _write(tmp_path / "labelled-00000-of-00002.parquet", [_record(_image(), _mask(()))])

    (row,) = list(GwfssAdapter(PALETTE).iter_rows([path]))

    assert row.mask_classes == ()


def test_the_mask_must_agree_with_the_image_about_size(tmp_path: Path) -> None:
    """A mask that does not lie over its photo records every class for the wrong pixels."""
    path = _write(
        tmp_path / "labelled-00000-of-00002.parquet",
        [_record(_image((4, 3)), _mask((HEAD,), size=(3, 4)))],
    )

    with pytest.raises(AdapterError, match="mask is 3x4 but the image is 4x3"):
        list(GwfssAdapter(PALETTE).iter_rows([path]))


def test_a_colour_outside_the_palette_stops_and_names_the_row(tmp_path: Path) -> None:
    """An undeclared colour means the mask and its legend disagree, so the run stops."""
    path = _write(
        tmp_path / "labelled-00000-of-00002.parquet", [_record(_image(), _mask((UNDECLARED,)))]
    )

    with pytest.raises(AdapterError, match="row 0"):
        list(GwfssAdapter(PALETTE).iter_rows([path]))


def test_a_row_without_a_mask_stops_and_names_the_row(tmp_path: Path) -> None:
    """The mask is the annotation; a row without one cannot be published as annotated."""
    path = _write(tmp_path / "labelled-00000-of-00002.parquet", [_record(_image(), b"")])

    with pytest.raises(AdapterError, match="no mask bytes"):
        list(GwfssAdapter(PALETTE).iter_rows([path]))


def test_unknown_magic_stops_and_names_the_row(tmp_path: Path) -> None:
    """A format the publisher cannot name must not become a guessed extension."""
    path = _write(tmp_path / "labelled-00000-of-00002.parquet", [_record(b"GIF89a", _mask(()))])

    with pytest.raises(UnknownImageFormat, match="row 0"):
        list(GwfssAdapter(PALETTE).iter_rows([path]))


def test_the_registry_needs_a_palette_for_the_mask_reader() -> None:
    """A vocabulary is provenance; without it the classes cannot be named, so this is loud."""
    with pytest.raises(MissingVocabulary, match="gwfss"):
        get_adapter("gwfss")


def test_the_registry_builds_the_mask_reader_with_a_palette() -> None:
    """Given the vocabulary, the mask schema is served like any other."""
    assert get_adapter("gwfss", PALETTE).mask_kind == "semantic"
