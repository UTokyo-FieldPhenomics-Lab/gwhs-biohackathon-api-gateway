"""Tests for the GWHS adapter, against a synthetic parquet with real metadata.

The synthetic file reproduces the parts of the upstream schema the adapter depends on: the
file-level Hugging Face ClassLabel metadata, the nested object struct, and the one-hot
columns it must ignore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from biohack_datasets.adapters import AdapterNotImplemented, get_adapter
from biohack_datasets.adapters.common import AdapterError
from biohack_datasets.adapters.gwhs import GwhsAdapter
from biohack_datasets.formats import UnknownImageFormat

JPEG = b"\xff\xd8\xff\xe0payload"
PNG = b"\x89PNG\r\n\x1a\npayload"


def _features() -> dict[str, object]:
    """Return the ClassLabel metadata of the synthetic upstream."""
    return {
        "image": {"_type": "Image"},
        "image_id": {"dtype": "string", "_type": "Value"},
        "width": {"dtype": "int32", "_type": "Value"},
        "height": {"dtype": "int32", "_type": "Value"},
        "tags": {"feature": {"names": ["Labo", "healthy"], "_type": "ClassLabel"}, "_type": "List"},
        "objects": {
            "feature": {
                "category": {"names": ["side1", "terminal"], "_type": "ClassLabel"},
                "bbox": {
                    "feature": {"dtype": "float32", "_type": "Value"},
                    "length": 4,
                    "_type": "List",
                },
                "rle": {
                    "size": {
                        "feature": {"dtype": "int32", "_type": "Value"},
                        "length": 2,
                        "_type": "List",
                    },
                    "counts": {"dtype": "string", "_type": "Value"},
                },
                "polygons": {
                    "feature": {"feature": {"dtype": "float32", "_type": "Value"}, "_type": "List"},
                    "_type": "List",
                },
                "area": {"dtype": "float32", "_type": "Value"},
                "attributes": {
                    "feature": {"names": ["Green-Spikelet", "occluded"], "_type": "ClassLabel"},
                    "_type": "List",
                },
                "attributes_onehot": {
                    "feature": {"dtype": "int8", "_type": "Value"},
                    "length": 5,
                    "_type": "List",
                },
            },
            "_type": "List",
        },
    }


def _schema(with_metadata: bool = True) -> pa.Schema:
    """Return the synthetic upstream's Arrow schema."""
    rle = pa.struct([("size", pa.list_(pa.int32(), 2)), ("counts", pa.string())])
    obj = pa.struct(
        [
            ("category", pa.int64()),
            ("bbox", pa.list_(pa.float32(), 4)),
            ("rle", rle),
            ("polygons", pa.list_(pa.list_(pa.float32()))),
            ("area", pa.float32()),
            ("attributes", pa.list_(pa.int64())),
            ("attributes_onehot", pa.list_(pa.int8(), 5)),
        ]
    )
    schema = pa.schema(
        [
            ("image", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            ("image_id", pa.string()),
            ("width", pa.int32()),
            ("height", pa.int32()),
            ("tags", pa.list_(pa.int64())),
            ("tags_onehot", pa.list_(pa.int8(), 18)),
            ("objects", pa.list_(obj)),
        ]
    )
    if not with_metadata:
        return schema
    return schema.with_metadata({"huggingface": json.dumps({"info": {"features": _features()}})})


def _record(body: bytes, name: str, tags: list[int]) -> dict[str, object]:
    """Return one synthetic parquet record."""
    return {
        "image": {"bytes": body, "path": name},
        "image_id": name,
        "width": 4,
        "height": 3,
        "tags": tags,
        "tags_onehot": [0] * 18,
        "objects": [
            {
                "category": 1,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "rle": {"size": [3, 4], "counts": "Z0"},
                "polygons": [[0.0, 0.0, 1.0, 1.0]],
                "area": 12.0,
                "attributes": [0],
                "attributes_onehot": [0] * 5,
            }
        ],
    }


def _write(path: Path, records: list[dict[str, object]], with_metadata: bool = True) -> Path:
    """Write records as one parquet file and return its path."""
    table = pa.Table.from_pylist(records, schema=_schema(with_metadata))
    pq.write_table(table, path)
    return path


def test_names_and_geometry_come_out_of_the_synthetic_upstream(tmp_path: Path) -> None:
    """Tags, categories, and attributes resolve through the file-level metadata."""
    path = _write(tmp_path / "train-00000-of-00001.parquet", [_record(JPEG, "a.JPG", [0, 1])])
    (row,) = list(GwhsAdapter().iter_rows([path]))

    assert row.row == 0
    assert row.split == "train"
    assert row.image_ext == "jpg"
    assert row.image == JPEG
    assert row.width == 4 and row.height == 3
    assert row.image_id == "a.JPG"
    assert row.tags == ("Labo", "healthy")
    assert len(row.objects) == 1
    annotation = row.objects[0]
    assert annotation.idx == 0
    assert annotation.category == "terminal"
    assert annotation.bbox == (1.0, 2.0, 3.0, 4.0)
    assert annotation.area == 12.0
    assert annotation.attributes == ("Green-Spikelet",)
    assert annotation.rle is not None
    assert (annotation.rle.height, annotation.rle.width, annotation.rle.counts) == (3, 4, "Z0")


def test_extension_is_sniffed_per_row(tmp_path: Path) -> None:
    """One dataset may mix encodings, so the extension is a per-row fact."""
    path = _write(
        tmp_path / "train-00000-of-00001.parquet",
        [_record(JPEG, "a.JPG", [0]), _record(PNG, "b.png", [1])],
    )
    rows = list(GwhsAdapter().iter_rows([path]))

    assert [row.image_ext for row in rows] == ["jpg", "png"]


def test_row_numbers_are_cumulative_over_sorted_files(tmp_path: Path) -> None:
    """The absolute row is the definition of an address, so file order decides it."""
    first = _write(tmp_path / "train-00000-of-00001.parquet", [_record(JPEG, "a.JPG", [0])])
    second = _write(tmp_path / "validation-00000-of-00001.parquet", [_record(PNG, "b.png", [1])])
    rows = list(GwhsAdapter().iter_rows([first, second]))

    assert [(row.row, row.split) for row in rows] == [(0, "train"), (1, "validation")]


def test_unknown_magic_stops_and_names_the_row(tmp_path: Path) -> None:
    """A format the publisher cannot name must not become a guessed extension."""
    path = _write(tmp_path / "train-00000-of-00001.parquet", [_record(b"GIF89a", "a.gif", [0])])

    with pytest.raises(UnknownImageFormat, match="row 0"):
        list(GwhsAdapter().iter_rows([path]))


def test_a_file_without_class_label_metadata_is_refused(tmp_path: Path) -> None:
    """The metadata is the only place the names exist; absent it, guessing would invent."""
    path = _write(
        tmp_path / "train-00000-of-00001.parquet",
        [_record(JPEG, "a.JPG", [0])],
        with_metadata=False,
    )

    with pytest.raises(AdapterError, match="huggingface"):
        list(GwhsAdapter().iter_rows([path]))


def test_the_registry_names_an_unimplemented_adapter() -> None:
    """An unimplemented adapter is a hard error naming it, never a silent skip."""
    assert get_adapter("gwhs").mask_kind is None

    with pytest.raises(AdapterNotImplemented, match="nonesuch"):
        get_adapter("nonesuch")
