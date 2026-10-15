"""Tests for building, validating, and reading the SQLite metadata index."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import read_back

from biohack_datasets.index import IndexValidationError, build, commit
from biohack_datasets.model import Annotation, Rle, Row
from biohack_datasets.sources import Source, Upstream

REVISION = "655118534e9ad980b6f7c0dd6566a406d5a29a27"
SOURCE = Source(
    "gwhs-temp",
    Upstream("GWHS/sample_dataset", REVISION, ("data/*.parquet",)),
    "gwhs",
    None,
    None,
)
GENERATED_AT = "2026-10-15T09:00:00Z"


def _row(**overrides: Any) -> Row:
    """Return one valid neutral row, with fields overridable for a refusal test."""
    values: dict[str, Any] = {
        "row": 0,
        "split": "train",
        "image_ext": "jpg",
        "width": 4,
        "height": 3,
        "image": b"\xff\xd8\xff\x00",
        "image_id": "a.JPG",
        "tags": ("Labo", "healthy"),
        "objects": (
            Annotation(
                0,
                "spikelet_-_side1",
                (1.0, 2.0, 3.0, 4.0),
                area=12.0,
                attributes=("Green-Spikelet",),
                rle=Rle(3, 4, "Z0"),
            ),
        ),
    }
    values.update(overrides)
    return Row(**values)


def _build(tmp_path: Path, rows: Iterator[Row] | list[Row] | tuple[Row, ...]) -> Path:
    """Build an index for the fixture source and return its final path."""
    destination = tmp_path / "gwhs-temp.db"
    temporary = build(
        SOURCE,
        rows,
        destination,
        rules="0.1.0",
        generated_at=GENERATED_AT,
        mask_kind=None,
    )
    commit(temporary, destination)
    return destination


def test_every_table_round_trips(tmp_path: Path) -> None:
    """One row and its object land in every table the schema declares."""
    index = _build(tmp_path, [_row()])

    tables = read_back(index)

    assert tables["dataset"] == [("gwhs-temp", REVISION, "0.1.0", None, None, GENERATED_AT)]
    assert tables["rows"] == [(0, "train", "jpg", None, 4, 3, "a.JPG", None, None, None, None)]
    assert tables["tags"] == [(0, "Labo"), (0, "healthy")]
    assert tables["objects"] == [(0, 0, "spikelet_-_side1", 1.0, 2.0, 3.0, 4.0, 12.0)]
    assert tables["object_attributes"] == [(0, 0, "Green-Spikelet")]
    assert tables["rles"] == [(0, 0, 3, 4, "Z0")]
    assert tables["classes"] == []
    assert tables["row_classes"] == []


def test_an_area_the_upstream_does_not_publish_stays_null(tmp_path: Path) -> None:
    """The publisher never computes an area; a dataset without one stores NULL."""
    object_without_area = Annotation(0, "0", (1, 2, 3, 4), area=None)
    index = _build(tmp_path, [_row(objects=(object_without_area,))])

    assert read_back(index)["objects"] == [(0, 0, "0", 1.0, 2.0, 3.0, 4.0, None)]


def test_a_non_contiguous_row_is_refused_and_named(tmp_path: Path) -> None:
    """Rows are the identity, so a gap is a build failure, not a sorting problem."""
    with pytest.raises(IndexValidationError, match="row 1"):
        _build(tmp_path, [_row(row=0), _row(row=2)])


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"width": 0}, "width and height"),
        ({"image_ext": "gif"}, "image_ext"),
        ({"split": ""}, "split"),
        ({"objects": (Annotation(0, "", (0, 0, 1, 1)),)}, "empty category"),
        (
            {"objects": (Annotation(0, "c", (0, 0, 1, 1), rle=Rle(9, 9, "Z0")),)},
            "image is",
        ),
        (
            {"objects": (Annotation(0, "c", (0, 0, 1, 1), rle=Rle(3, 4, "")),)},
            "empty RLE counts",
        ),
        ({"tags": ("",)}, "empty tag"),
    ],
)
def test_a_row_that_breaks_the_contract_is_refused(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    """Every NOT NULL, extension, density, and RLE invariant stops the build."""
    with pytest.raises(IndexValidationError, match=message):
        _build(tmp_path, [_row(**overrides)])


def test_a_rebuild_reads_back_identically(tmp_path: Path) -> None:
    """Deterministic content: same revision, same timestamp, same canonical read-back."""
    first = _build(tmp_path / "one", [_row()])
    second = _build(tmp_path / "two", [_row()])

    assert read_back(first) == read_back(second)


def test_the_index_replaces_atomically(tmp_path: Path) -> None:
    """Build writes beside the target; the destination appears only at commit."""
    destination = tmp_path / "gwhs-temp.db"
    temporary = build(SOURCE, [_row()], destination, rules="0.1.0", generated_at=GENERATED_AT)

    assert not destination.exists()
    assert temporary.parent == destination.parent

    commit(temporary, destination)

    assert destination.exists() and not temporary.exists()


def test_a_failed_build_leaves_no_partial_file(tmp_path: Path) -> None:
    """A refusal deletes the temporary file, so no half-written index survives."""
    destination = tmp_path / "gwhs-temp.db"
    with pytest.raises(IndexValidationError):
        build(SOURCE, [_row(width=0)], destination, rules="0.1.0", generated_at=GENERATED_AT)

    assert list(tmp_path.glob("*.tmp")) == []
    assert not destination.exists()


def test_the_file_is_a_plain_sqlite_database(tmp_path: Path) -> None:
    """No WAL sidecar: the index is one file, because it is moved and copied as one."""
    index = _build(tmp_path, [_row()])
    connection = sqlite3.connect(index)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        connection.close()
