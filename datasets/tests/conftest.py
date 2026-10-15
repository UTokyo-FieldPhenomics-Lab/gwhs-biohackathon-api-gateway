"""Shared test helpers.

``read_back`` is the canonical ordered read of a built index: it is what makes "a rebuild is
deterministic" a checkable claim rather than a promise, so it reads every table by primary key and
compares. It lives here rather than in the package because nothing in a publish reads an index back
— the publisher writes and the gateway reads — and a second reader in the package would be a second
place the column list has to stay true.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from biohack_datasets.index import TABLES

QUERIES = {
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
"""One canonical query per table, ordered by primary key."""


def read_back(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    """Read every table in a canonical order, for comparison.

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
    connection = sqlite3.connect(path)
    try:
        return {table: connection.execute(QUERIES[table]).fetchall() for table in TABLES}
    finally:
        connection.close()
