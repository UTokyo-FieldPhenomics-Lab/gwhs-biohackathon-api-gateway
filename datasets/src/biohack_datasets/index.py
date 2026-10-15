"""Build, validate, and read one dataset's SQLite metadata index.

The index is the whole metadata plane of one dataset: eight tables, one row per image, and
the annotation geometry the API returns on demand. It is built from the pinned upstream
revision, written beside its target, and moved into place with :func:`os.replace`, so a
reader never sees a half-written file.

Counts are queries, not stored facts, and there is no manifest: the single `dataset` row
carries the provenance a manifest used to hold, and every other number is derived from the
rows. Determinism is content, not bytes: SQLite's page layout is version-dependent, so the
guarantee is a canonical ordered read-back.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from biohack_datasets.formats import EXTENSIONS
from biohack_datasets.model import Row
from biohack_datasets.sources import Source

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

TABLES = (
    "dataset",
    "classes",
    "rows",
    "row_classes",
    "tags",
    "objects",
    "object_attributes",
    "rles",
)
"""Every table of the index, in the order :func:`read_back` reads them."""


class IndexValidationError(RuntimeError):
    """A row violates the index contract; the message names the offending row."""


def utc_timestamp() -> str:
    """Return the current UTC time in the form the index stores.

    Returns
    -------
    str
        ISO 8601 with a trailing `Z`, second resolution.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(
    source: Source,
    rows: Iterable[Row],
    destination: Path,
    *,
    rules: str,
    generated_at: str,
    classes: Sequence[tuple[int, str, str]] = (),
    mask_kind: str | None = None,
) -> Path:
    """Build a complete index beside `destination` and return its temporary path.

    Parameters
    ----------
    source : Source
        Dataset being published. Its pinned revision is what the index records, and a
        dataset without a pinned upstream cannot be built.
    rows : iterable of Row
        Neutral rows in absolute row order, starting at zero.
    destination : pathlib.Path
        Final path the caller will move the result onto with :func:`commit`.
    rules : str
        Installed `biohack-datasets` version.
    generated_at : str
        Timestamp recorded on the `dataset` row. Injected so a rebuild can be compared.
    classes : sequence of tuple
        `(id, name, color)` of the dataset's mask palette, if it has one.
    mask_kind : str or None
        `"semantic"`, `"instance"`, or `None` when the dataset ships no mask object.

    Returns
    -------
    pathlib.Path
        The validated temporary file. It is beside `destination`, so the move is atomic.

    Raises
    ------
    IndexValidationError
        A row breaks the contract, or the pinned revision is absent.
    """
    if source.upstream is None:
        raise IndexValidationError(f"{source.dataset_id}: no pinned upstream revision to publish")

    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(name)

    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT INTO dataset (id, revision, rules, license, mask_kind, generated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    source.dataset_id,
                    source.upstream.revision,
                    rules,
                    source.license,
                    mask_kind,
                    generated_at,
                ),
            )
            for class_id, name_, color in classes:
                connection.execute(
                    "INSERT INTO classes (id, name, color) VALUES (?, ?, ?)",
                    (class_id, name_, color),
                )
            _insert_rows(connection, rows)
            # VACUUM cannot run inside a transaction, so the inserts commit first.
            connection.commit()
            connection.execute("VACUUM")
        finally:
            connection.close()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def commit(temporary: Path, destination: Path) -> None:
    """Move a built index onto its final path.

    Parameters
    ----------
    temporary : pathlib.Path
        Path returned by :func:`build`.
    destination : pathlib.Path
        Final path. `os.replace` makes the swap atomic within one filesystem.
    """
    os.replace(temporary, destination)


def read_back(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Read every table in a canonical order, for comparison and tests.

    Parameters
    ----------
    path : pathlib.Path
        Index to read.

    Returns
    -------
    dict of str to list of tuple
        One entry per table, rows ordered by primary key. Two indexes built from the same
        revision with the same `generated_at` read back identically.
    """
    queries = {
        "dataset": (
            "SELECT id, revision, rules, license, mask_kind, generated_at FROM dataset ORDER BY id"
        ),
        "classes": "SELECT id, name, color FROM classes ORDER BY id",
        "rows": (
            "SELECT row, split, image_ext, mask_ext, width, height, image_id, domain, country,"
            " location, development_stage FROM rows ORDER BY row"
        ),
        "row_classes": "SELECT row, class_id FROM row_classes ORDER BY row, class_id",
        "tags": "SELECT row, tag FROM tags ORDER BY row, tag",
        "objects": "SELECT row, idx, category, x, y, w, h, area FROM objects ORDER BY row, idx",
        "object_attributes": (
            "SELECT row, idx, attribute FROM object_attributes ORDER BY row, idx, attribute"
        ),
        "rles": "SELECT row, idx, height, width, counts FROM rles ORDER BY row, idx",
    }
    connection = sqlite3.connect(path)
    try:
        return {table: connection.execute(queries[table]).fetchall() for table in TABLES}
    finally:
        connection.close()


def _insert_rows(connection: sqlite3.Connection, rows: Iterable[Row]) -> None:
    """Validate and insert every neutral row, in the order the adapter yields them.

    Parameters
    ----------
    connection : sqlite3.Connection
        Open connection to the index under construction.
    rows : iterable of Row
        Rows in absolute row order, starting at zero.

    Raises
    ------
    IndexValidationError
        A row is out of order or breaks a NOT NULL, extension, index-density, or RLE
        invariant. The message names the row.
    """
    for expected, row in enumerate(rows):
        if row.row != expected:
            raise IndexValidationError(
                f"row {row.row}: expected absolute row {expected}, rows must be contiguous from 0"
            )
        _validate(row)
        _insert_row(connection, row)


def _validate(row: Row) -> None:
    """Check one row against the index contract.

    Parameters
    ----------
    row : Row
        Row to check.

    Raises
    ------
    IndexValidationError
        The row breaks an invariant.
    """
    where = f"row {row.row}"
    if not row.split:
        raise IndexValidationError(f"{where}: split is empty")
    if row.image_ext not in EXTENSIONS:
        raise IndexValidationError(
            f"{where}: image_ext {row.image_ext!r} is not one of {EXTENSIONS}"
        )
    if row.mask_ext is not None and row.mask_ext not in EXTENSIONS:
        raise IndexValidationError(f"{where}: mask_ext {row.mask_ext!r} is not one of {EXTENSIONS}")
    if row.width <= 0 or row.height <= 0:
        raise IndexValidationError(
            f"{where}: width and height must be positive, got {row.width}x{row.height}"
        )
    if not row.image:
        raise IndexValidationError(f"{where}: image bytes are empty")
    if row.mask_ext is not None and not row.mask:
        raise IndexValidationError(f"{where}: mask_ext is set but mask bytes are empty")

    for idx, annotation in enumerate(row.objects):
        if annotation.idx != idx:
            raise IndexValidationError(f"{where}: object idx {annotation.idx} is not dense from 0")
        if not annotation.category:
            raise IndexValidationError(f"{where}: object {idx} has an empty category")
        for attribute in annotation.attributes:
            if not attribute:
                raise IndexValidationError(f"{where}: object {idx} has an empty attribute")
        if annotation.rle is None:
            continue
        if not annotation.rle.counts:
            raise IndexValidationError(f"{where}: object {idx} has empty RLE counts")
        if annotation.rle.height != row.height or annotation.rle.width != row.width:
            raise IndexValidationError(
                f"{where}: object {idx} RLE is {annotation.rle.height}x{annotation.rle.width},"
                f" image is {row.height}x{row.width}"
            )

    for tag in row.tags:
        if not tag:
            raise IndexValidationError(f"{where}: empty tag name")


def _insert_row(connection: sqlite3.Connection, row: Row) -> None:
    """Insert one validated row and its annotation into every table that applies.

    Parameters
    ----------
    connection : sqlite3.Connection
        Open connection to the index under construction.
    row : Row
        Validated neutral row.
    """
    connection.execute(
        "INSERT INTO rows (row, split, image_ext, mask_ext, width, height, image_id, domain,"
        " country, location, development_stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row.row,
            row.split,
            row.image_ext,
            row.mask_ext,
            row.width,
            row.height,
            row.image_id,
            row.domain,
            row.country,
            row.location,
            row.development_stage,
        ),
    )
    for tag in row.tags:
        connection.execute("INSERT INTO tags (row, tag) VALUES (?, ?)", (row.row, tag))
    for class_id in row.mask_classes:
        connection.execute(
            "INSERT INTO row_classes (row, class_id) VALUES (?, ?)", (row.row, class_id)
        )
    for annotation in row.objects:
        x, y, w, h = annotation.bbox
        connection.execute(
            "INSERT INTO objects (row, idx, category, x, y, w, h, area)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row.row, annotation.idx, annotation.category, x, y, w, h, annotation.area),
        )
        for attribute in annotation.attributes:
            connection.execute(
                "INSERT INTO object_attributes (row, idx, attribute) VALUES (?, ?, ?)",
                (row.row, annotation.idx, attribute),
            )
        if annotation.rle is not None:
            connection.execute(
                "INSERT INTO rles (row, idx, height, width, counts) VALUES (?, ?, ?, ?, ?)",
                (
                    row.row,
                    annotation.idx,
                    annotation.rle.height,
                    annotation.rle.width,
                    annotation.rle.counts,
                ),
            )


def counts(path: Path) -> tuple[int, int]:
    """Return the number of image rows and object annotations in an index.

    Parameters
    ----------
    path : pathlib.Path
        Index to read.

    Returns
    -------
    tuple of (int, int)
        Row count and object count.
    """
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute("SELECT count(*) FROM rows").fetchone()[0]
        objects = connection.execute("SELECT count(*) FROM objects").fetchone()[0]
    finally:
        connection.close()
    return int(rows), int(objects)
