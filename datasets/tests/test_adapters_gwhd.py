"""Tests for the GWHD adapter, against a synthetic parquet with the measured schema.

The synthetic file reproduces what the pinned upstream actually declares: an `image` struct, four
string metadata columns, and an `objects` struct holding two parallel lists. There is no `width` and
no `height` column, which is why the dimensions are read from the image's own header — so the tests
build real image bytes rather than magic prefixes.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from PIL import Image

from biohack_datasets.adapters import get_adapter
from biohack_datasets.adapters.common import AdapterError
from biohack_datasets.adapters.gwhd import GwhdAdapter
from biohack_datasets.formats import UnknownImageFormat


def _image(size: tuple[int, int] = (8, 6), fmt: str = "JPEG") -> bytes:
    """Return real image bytes, so the header the adapter reads is a real one."""
    buffer = BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


def _schema() -> pa.Schema:
    """Return the synthetic upstream's Arrow schema, as the pinned file declares it."""
    return pa.schema(
        [
            ("image", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            ("domain", pa.string()),
            ("country", pa.string()),
            ("location", pa.string()),
            ("development_stage", pa.string()),
            (
                "objects",
                pa.struct(
                    [
                        ("boxes", pa.list_(pa.list_(pa.int64()))),
                        ("categories", pa.list_(pa.int64())),
                    ]
                ),
            ),
        ]
    )


def _record(body: bytes, boxes: list[list[int]], categories: list[int]) -> dict[str, object]:
    """Return one synthetic parquet record."""
    return {
        "image": {"bytes": body, "path": "a.jpg"},
        "domain": "Arvalis_11",
        "country": "France",
        "location": "VLB",
        "development_stage": "Filling",
        "objects": {"boxes": boxes, "categories": categories},
    }


def _write(path: Path, records: list[dict[str, object]]) -> Path:
    """Write records as one parquet file and return its path."""
    pq.write_table(pa.Table.from_pylist(records, schema=_schema()), path)
    return path


def test_boxes_metadata_and_dimensions_come_out_of_the_synthetic_upstream(tmp_path: Path) -> None:
    """The category is the upstream integer in text, and the size is the image's own header."""
    path = _write(
        tmp_path / "train-00000-of-00002.parquet",
        [_record(_image((8, 6)), [[1, 2, 3, 4], [5, 6, 7, 8]], [0, 0])],
    )
    (row,) = list(GwhdAdapter().iter_rows([path]))

    assert row.row == 0
    assert row.split == "train"
    assert row.image_ext == "jpg"
    assert (row.width, row.height) == (8, 6)
    assert row.domain == "Arvalis_11"
    assert row.country == "France"
    assert row.location == "VLB"
    assert row.development_stage == "Filling"
    assert row.mask is None and row.mask_ext is None and row.mask_classes == ()
    assert row.tags == ()
    assert [annotation.category for annotation in row.objects] == ["0", "0"]
    assert row.objects[0].bbox == (1.0, 2.0, 3.0, 4.0)
    assert row.objects[0].area is None
    assert row.objects[0].attributes == ()
    assert row.objects[0].rle is None


def test_an_empty_object_list_is_a_row_with_no_annotation(tmp_path: Path) -> None:
    """A row the upstream published without heads is still a row."""
    path = _write(tmp_path / "train-00000-of-00002.parquet", [_record(_image(), [], [])])

    (row,) = list(GwhdAdapter().iter_rows([path]))

    assert row.objects == ()


def test_a_png_row_is_sniffed_per_row(tmp_path: Path) -> None:
    """A dataset may mix encodings, so the extension is a per-row fact."""
    path = _write(
        tmp_path / "train-00000-of-00002.parquet",
        [_record(_image(fmt="PNG"), [], []), _record(_image(fmt="JPEG"), [], [])],
    )
    rows = list(GwhdAdapter().iter_rows([path]))

    assert [row.image_ext for row in rows] == ["png", "jpg"]


def test_row_numbers_are_cumulative_over_sorted_files(tmp_path: Path) -> None:
    """The absolute row is the definition of an address, so file order decides it."""
    first = _write(tmp_path / "train-00000-of-00002.parquet", [_record(_image(), [], [])])
    second = _write(tmp_path / "validation-00000-of-00001.parquet", [_record(_image(), [], [])])

    rows = list(GwhdAdapter().iter_rows([first, second]))

    assert [(row.row, row.split) for row in rows] == [(0, "train"), (1, "validation")]


def test_boxes_without_categories_stop_and_name_the_row(tmp_path: Path) -> None:
    """Two parallel lists that disagree mean the row's annotation is not self-consistent."""
    path = _write(
        tmp_path / "train-00000-of-00002.parquet", [_record(_image(), [[1, 2, 3, 4]], [])]
    )

    with pytest.raises(AdapterError, match="row 0"):
        list(GwhdAdapter().iter_rows([path]))


def test_a_box_that_is_not_four_numbers_stops_and_names_the_row(tmp_path: Path) -> None:
    """A box the schema promised as four numbers is not silently truncated."""
    path = _write(tmp_path / "train-00000-of-00002.parquet", [_record(_image(), [[1, 2, 3]], [0])])

    with pytest.raises(AdapterError, match="row 0"):
        list(GwhdAdapter().iter_rows([path]))


def test_unknown_magic_stops_and_names_the_row(tmp_path: Path) -> None:
    """A format the publisher cannot name must not become a guessed extension."""
    path = _write(tmp_path / "train-00000-of-00002.parquet", [_record(b"GIF89a", [], [])])

    with pytest.raises(UnknownImageFormat, match="row 0"):
        list(GwhdAdapter().iter_rows([path]))


def test_the_registry_builds_the_gwhd_reader_without_a_vocabulary() -> None:
    """Boxes need no palette, so this reader is never blocked on one."""
    assert get_adapter("gwhd").mask_kind is None
