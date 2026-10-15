"""One synthetic index directory, built from the publisher's frozen schema.

The gateway reads the artifact `biohack-datasets publish` writes, so the tests build one here with
the eight tables of `docs/content/data-layout.md` rather than importing the publisher package: the gateway
has no runtime dependency on it, and a fixture transcribed from the handwritten contract is what
turns a schema drift into a failing test instead of a wrong answer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

REVISION = "a" * 40
"""The pinned upstream revision every fixture dataset records."""

GENERATED_AT = "2026-10-15T09:00:00Z"
"""One build timestamp, so a description can be compared whole."""

SCHEMA = """
CREATE TABLE dataset (
  id           TEXT PRIMARY KEY,
  revision     TEXT NOT NULL,
  rules        TEXT NOT NULL,
  license      TEXT,
  mask_kind    TEXT,
  generated_at TEXT NOT NULL
);
CREATE TABLE classes (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT NOT NULL
);
CREATE TABLE rows (
  row       INTEGER PRIMARY KEY,
  split     TEXT    NOT NULL,
  image_ext TEXT    NOT NULL,
  mask_ext  TEXT,
  width     INTEGER NOT NULL,
  height    INTEGER NOT NULL,
  image_id  TEXT,
  domain TEXT, country TEXT, location TEXT, development_stage TEXT
);
CREATE TABLE row_classes (
  row INTEGER NOT NULL, class_id INTEGER NOT NULL, PRIMARY KEY (row, class_id)
) WITHOUT ROWID;
CREATE TABLE tags (
  row INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (row, tag)
) WITHOUT ROWID;
CREATE TABLE objects (
  row INTEGER NOT NULL, idx INTEGER NOT NULL,
  category TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  area REAL,
  PRIMARY KEY (row, idx)
) WITHOUT ROWID;
CREATE TABLE object_attributes (
  row INTEGER NOT NULL, idx INTEGER NOT NULL, attribute TEXT NOT NULL,
  PRIMARY KEY (row, idx, attribute)
) WITHOUT ROWID;
CREATE TABLE rles (
  row INTEGER NOT NULL, idx INTEGER NOT NULL,
  height INTEGER NOT NULL, width INTEGER NOT NULL, counts TEXT NOT NULL,
  PRIMARY KEY (row, idx)
) WITHOUT ROWID;
"""


def write_index(
    path: Path,
    *,
    dataset: tuple[str, str, str, str | None, str | None, str],
    rows: Sequence[tuple[Any, ...]] = (),
    classes: Sequence[tuple[int, str, str]] = (),
    row_classes: Sequence[tuple[int, int]] = (),
    tags: Sequence[tuple[int, str]] = (),
    objects: Sequence[tuple[Any, ...]] = (),
    attributes: Sequence[tuple[int, int, str]] = (),
    rles: Sequence[tuple[Any, ...]] = (),
) -> Path:
    """Write one index file and return its path.

    Parameters
    ----------
    path : Path
        Where to create the SQLite file, conventionally `<dataset>.db`.
    dataset : tuple
        The single `dataset` row: id, revision, rules, license, mask_kind, generated_at.
    rows, classes, row_classes, tags, objects, attributes, rles : sequence, optional
        One table's rows, inserted verbatim. They are untyped tuples on purpose: the point is to
        write exactly what a publisher would, not to model it twice.

    Returns
    -------
    Path
        The same path, so a test can name what it wrote.
    """
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO dataset VALUES (?, ?, ?, ?, ?, ?)", dataset)
        connection.executemany("INSERT INTO classes VALUES (?, ?, ?)", classes)
        connection.executemany("INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        connection.executemany("INSERT INTO row_classes VALUES (?, ?)", row_classes)
        connection.executemany("INSERT INTO tags VALUES (?, ?)", tags)
        connection.executemany("INSERT INTO objects VALUES (?, ?, ?, ?, ?, ?, ?, ?)", objects)
        connection.executemany("INSERT INTO object_attributes VALUES (?, ?, ?)", attributes)
        connection.executemany("INSERT INTO rles VALUES (?, ?, ?, ?, ?)", rles)
        connection.commit()
    finally:
        connection.close()
    return path


@pytest.fixture
def index_dir(tmp_path: Path) -> Path:
    """Give a directory holding one dataset of each shape the surface answers for.

    `gwhd` is boxes with row metadata, `gwfss` is a semantic mask with a palette, and
    `gwhs-temp` is objects with RLE geometry, tags, and attributes.
    """
    directory = tmp_path / "index"
    directory.mkdir()
    write_index(
        directory / "gwhd.db",
        dataset=("gwhd", REVISION, "0.1.0", None, None, GENERATED_AT),
        rows=(
            (0, "train", "jpg", None, 1024, 1024, None, "field", "Germany", "Einbeck", "tillering"),
            (
                1,
                "validation",
                "jpg",
                None,
                512,
                512,
                None,
                "field",
                "France",
                None,
                "stem_elongation",
            ),
        ),
        objects=(
            (0, 0, "0", 936.0, 4.0, 41.0, 78.0, None),
            (0, 1, "0", 10.0, 20.0, 30.0, 40.0, None),
            (1, 0, "0", 5.0, 6.0, 7.0, 8.0, None),
        ),
    )
    write_index(
        directory / "gwfss.db",
        dataset=("gwfss", REVISION, "0.1.0", "cc-by-4.0", "semantic", GENERATED_AT),
        classes=(
            (0, "background", "#000000"),
            (1, "head", "#32ff84"),
            (2, "stem", "#3284ff"),
            (3, "leaf", "#d6ff32"),
        ),
        rows=(
            (0, "labelled", "png", "png", 512, 512, None, None, None, None, None),
            (1, "labelled", "png", "png", 1024, 1024, None, None, None, None, None),
        ),
        row_classes=((0, 1), (0, 3), (1, 1), (1, 2), (1, 3)),
    )
    write_index(
        directory / "gwhs-temp.db",
        dataset=("gwhs-temp", REVISION, "0.1.0", None, None, GENERATED_AT),
        rows=(
            (0, "train", "jpg", None, 592, 2509, "149_ear_001.JPG", None, None, None, None),
            (1, "train", "png", None, 600, 300, "150_ear_002.png", None, None, None, None),
        ),
        tags=((0, "Labo"), (0, "healthy"), (1, "Labo")),
        objects=(
            (0, 0, "spikelet_-_side1", 231.0, 923.0, 126.0, 162.0, 12074.0),
            (0, 1, "spikelet_-_side2", 208.0, 939.0, 41.0, 77.0, 1222.0),
            (1, 0, "spikelet_-_side1", 1.0, 2.0, 3.0, 4.0, 12.0),
        ),
        attributes=((0, 0, "Green-Spikelet"), (0, 1, "Green-Spikelet")),
        rles=((0, 0, 2509, 592, "abc"), (0, 1, 2509, 592, "def"), (1, 0, 300, 600, "ghi")),
    )
    return directory
