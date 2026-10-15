"""The publisher's metadata index, read once at startup and answered from memory.

One SQLite file per dataset under a host directory, written by `biohack-datasets publish` and frozen
by `docs/data-layout.md`. The gateway reads that artifact and never writes it, augments it, or
re-derives it, and nothing reads the file again after startup — which is what makes a republish take
effect through a restart and nothing else.

The whole index is loaded because the surface answers from memory: a connection per request would
make the restart contract false, would need a lock once handlers run on more than one thread, and
would put a filesystem path inside the request path. Counts and facets are computed in the same
pass, so they are a projection of the index rather than a second store.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INDEX_DIR = Path.home() / ".config" / "biohack-gateway" / "index"
"""Where the gateway looks for `<dataset>.db` unless the host names another directory."""

_ROW_METADATA = ("domain", "country", "location", "development_stage")
"""The optional per-row fields a dataset may offer as filters, in the order facets are built."""

_SQL_DATASET = "SELECT id, revision, rules, license, mask_kind, generated_at FROM dataset"
_SQL_CLASSES = "SELECT id, name, color FROM classes ORDER BY id"
_SQL_ROWS = (
    "SELECT row, split, image_ext, mask_ext, width, height, image_id, domain, country, location,"
    " development_stage FROM rows ORDER BY row"
)
_SQL_ROW_CLASSES = "SELECT row, class_id FROM row_classes ORDER BY row, class_id"
_SQL_TAGS = "SELECT row, tag FROM tags ORDER BY row, tag"
_SQL_OBJECTS = "SELECT row, idx, category, x, y, w, h, area FROM objects ORDER BY row, idx"
_SQL_ATTRIBUTES = "SELECT row, idx, attribute FROM object_attributes ORDER BY row, idx, attribute"
_SQL_RLES = "SELECT row, idx, height, width, counts FROM rles ORDER BY row, idx"


class DatasetIndexError(RuntimeError):
    """An index file cannot be read as the contract describes, so the gateway must not start."""


@dataclass(frozen=True, slots=True)
class ClassInfo:
    """One entry of a dataset's mask palette.

    Attributes
    ----------
    id : int
        The class id the upstream assigned, and the key `row_classes` stores.
    name : str
        The class name, as the pinned vocabulary spells it.
    color : str
        The palette colour, `#rrggbb`.
    """

    id: int
    name: str
    color: str


@dataclass(frozen=True, slots=True)
class Rle:
    """One object's COCO run-length mask, verbatim from the upstream.

    Attributes
    ----------
    height, width : int
        The mask's dimensions, copied from the `size` the upstream published.
    counts : str
        The compressed string. Nothing here encodes or decodes one.
    """

    height: int
    width: int
    counts: str


@dataclass(frozen=True, slots=True)
class ImageObject:
    """One annotated object of a row.

    Attributes
    ----------
    idx : int
        The object's index inside its row, dense from zero.
    category : str
        The resolved class name, or the upstream integer as text where it named none.
    bbox : tuple of float
        `(x, y, w, h)`.
    area : float or None
        The upstream's own area, where it published one. Never computed here.
    attributes : tuple of str
        The upstream's attribute names, in sorted order.
    rle : Rle or None
        The object's geometry, where the dataset publishes it as RLE.
    """

    idx: int
    category: str
    bbox: tuple[float, float, float, float]
    area: float | None
    attributes: tuple[str, ...]
    rle: Rle | None


@dataclass(frozen=True, slots=True)
class ImageRow:
    """One image's identity and per-row metadata.

    Attributes
    ----------
    row : int
        The absolute row of the pinned upstream revision, and half of the address.
    split : str
        The split the row belongs to.
    image_ext, mask_ext : str or None
        The per-row object extensions the index sniffed; `mask_ext` is `None` when the dataset
        ships no mask.
    width, height : int
        The image's pixel dimensions.
    image_id : str or None
        The upstream's own id, where it has one. Carried, never claimed to be an address.
    domain, country, location, development_stage : str or None
        Row metadata, where the upstream carries it.
    """

    row: int
    split: str
    image_ext: str
    mask_ext: str | None
    width: int
    height: int
    image_id: str | None
    domain: str | None
    country: str | None
    location: str | None
    development_stage: str | None


@dataclass(frozen=True, slots=True)
class Page:
    """A slice of the rows a filter selected.

    Attributes
    ----------
    rows : tuple of ImageRow
        The page, in row order.
    next_offset : int or None
        The offset a following page starts at, or `None` when this page is the last.
    """

    rows: tuple[ImageRow, ...]
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class Dataset:
    """One dataset's whole index, in the shape the surface asks questions in.

    Attributes
    ----------
    id, revision, rules : str
        The dataset id, the pinned upstream revision, and the publisher version that built the file.
    license, mask_kind : str or None
        The license recorded in `sources.yaml`, and the kind of mask object the dataset ships.
    generated_at : str
        When the publisher built the index.
    classes : tuple of ClassInfo
        The palette, empty when the dataset has no class vocabulary.
    rows : tuple of ImageRow
        Every image, in row order.
    objects, tags, row_classes : mapping of int to tuple
        Per-row annotation, keyed by row number. `row_classes` holds resolved class names.
    filters : mapping of str to mapping of str to int
        Every filter key the dataset offers, with a row count per value.
    geometry : str or None
        `rle` when objects carry run-length masks, `boxes` when they carry only boxes.
    image_id_present : bool
        Whether any row carries the upstream's own id.
    metadata_keys : tuple of str
        The row metadata fields at least one row fills, which are the ones a response carries.
    row_index : mapping of int to ImageRow
        The same rows, keyed by row number, so an address is a lookup.
    """

    id: str
    revision: str
    rules: str
    license: str | None
    mask_kind: str | None
    generated_at: str
    classes: tuple[ClassInfo, ...]
    rows: tuple[ImageRow, ...]
    objects: Mapping[int, tuple[ImageObject, ...]]
    tags: Mapping[int, tuple[str, ...]]
    row_classes: Mapping[int, tuple[str, ...]]
    filters: Mapping[str, Mapping[str, int]]
    geometry: str | None
    image_id_present: bool
    metadata_keys: tuple[str, ...]
    row_index: Mapping[int, ImageRow]

    def select(self, filters: Sequence[tuple[str, str]], *, offset: int, limit: int) -> Page:
        """Slice the rows a filter selects.

        Parameters
        ----------
        filters : sequence of tuple
            `(key, value)` pairs from the query string. Every pair must hold, so repeating a key
            narrows rather than widens. A key this dataset does not offer selects nothing, which is
            the contract's answer and not an error.
        offset : int
            How many matches to skip, which is what a cursor carries.
        limit : int
            The page's ceiling.

        Returns
        -------
        Page
            The rows in index order, and the offset a following page starts at.
        """
        if any(key not in self.filters for key, _ in filters):
            return Page(rows=(), next_offset=None)
        matched = tuple(
            row
            for row in self.rows
            if all(self._matches(row, key, value) for key, value in filters)
        )
        page = matched[offset : offset + limit]
        following = offset + limit
        return Page(rows=page, next_offset=following if following < len(matched) else None)

    def _matches(self, row: ImageRow, key: str, value: str) -> bool:
        """Answer whether one row satisfies one filter pair.

        Parameters
        ----------
        row : ImageRow
            The row being tested.
        key, value : str
            One pair from the query string, already known to be a key this dataset offers.

        Returns
        -------
        bool
            Whether the row carries the value under that key.
        """
        if key == "split":
            return row.split == value
        if key in _ROW_METADATA:
            return row_metadata(row)[key] == value
        if key == "tags":
            return value in self.tags.get(row.row, ())
        if key == "category":
            return any(item.category == value for item in self.objects.get(row.row, ()))
        if key == "attribute":
            return any(value in item.attributes for item in self.objects.get(row.row, ()))
        if key == "class":
            return value in self.row_classes.get(row.row, ())
        return False


def row_metadata(row: ImageRow) -> dict[str, str | None]:
    """Name the optional per-row fields by the filter key that reaches each.

    Parameters
    ----------
    row : ImageRow
        The row to describe.

    Returns
    -------
    dict of str to str or None
        One entry per key in `_ROW_METADATA`, in that order.
    """
    return {
        "domain": row.domain,
        "country": row.country,
        "location": row.location,
        "development_stage": row.development_stage,
    }


def load_datasets(directory: Path) -> dict[str, Dataset]:
    """Read every `<dataset>.db` under a directory.

    Parameters
    ----------
    directory : pathlib.Path
        The host's index directory. It need not exist: the gateway starts before the first publish,
        and an empty index is a gateway serving nothing rather than a gateway that cannot start.

    Returns
    -------
    dict of str to Dataset
        One entry per readable index, keyed by the id its own `dataset` row carries and sorted by
        that id.

    Raises
    ------
    DatasetIndexError
        A file under the directory is not an index this contract describes. Starting with it would
        mean serving a wrong answer, which is worse than not starting.
    """
    if not directory.is_dir():
        return {}
    loaded = {dataset.id: dataset for dataset in map(_load, sorted(directory.glob("*.db")))}
    return dict(sorted(loaded.items()))


def _load(path: Path) -> Dataset:
    """Read one index file into memory.

    Parameters
    ----------
    path : pathlib.Path
        The SQLite file.

    Returns
    -------
    Dataset
        The dataset the file describes.

    Raises
    ------
    DatasetIndexError
        The file cannot be opened, or does not hold the tables this contract describes.
    """
    # Read-only, so a gateway process can never create or repair the publisher's artifact.
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise DatasetIndexError(f"{path}: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        try:
            return _read(connection, path)
        except sqlite3.Error as exc:
            raise DatasetIndexError(f"{path}: {exc}") from exc
    finally:
        connection.close()


def _read(connection: sqlite3.Connection, path: Path) -> Dataset:
    """Read the eight tables and assemble one dataset.

    Parameters
    ----------
    connection : sqlite3.Connection
        Open, read-only connection whose row factory is `sqlite3.Row`.
    path : pathlib.Path
        The file's path, so a refusal can name what is wrong.

    Returns
    -------
    Dataset
        The dataset the file describes.

    Raises
    ------
    DatasetIndexError
        The `dataset` table does not hold exactly one row, or a row names a class the palette
        does not define.
    """
    provenance = connection.execute(_SQL_DATASET).fetchall()
    if len(provenance) != 1:
        raise DatasetIndexError(
            f"{path}: expected exactly one dataset row, found {len(provenance)}"
        )
    dataset = dict(provenance[0])

    classes = tuple(ClassInfo(**dict(record)) for record in connection.execute(_SQL_CLASSES))
    rows = tuple(ImageRow(**dict(record)) for record in connection.execute(_SQL_ROWS))
    tags = _group_text(connection.execute(_SQL_TAGS), "tag")
    row_classes = _group_classes(connection.execute(_SQL_ROW_CLASSES), classes, path)

    attributes = _group_by_object(connection.execute(_SQL_ATTRIBUTES), "attribute")
    rles = {
        (record["row"], record["idx"]): Rle(
            height=record["height"], width=record["width"], counts=record["counts"]
        )
        for record in connection.execute(_SQL_RLES)
    }
    objects: dict[int, list[ImageObject]] = defaultdict(list)
    for record in connection.execute(_SQL_OBJECTS):
        key = (record["row"], record["idx"])
        objects[key[0]].append(
            ImageObject(
                idx=record["idx"],
                category=record["category"],
                bbox=(record["x"], record["y"], record["w"], record["h"]),
                area=record["area"],
                attributes=attributes.get(key, ()),
                rle=rles.get(key),
            )
        )
    frozen_objects = {key: tuple(value) for key, value in objects.items()}

    return Dataset(
        id=dataset["id"],
        revision=dataset["revision"],
        rules=dataset["rules"],
        license=dataset["license"],
        mask_kind=dataset["mask_kind"],
        generated_at=dataset["generated_at"],
        classes=classes,
        rows=rows,
        objects=frozen_objects,
        tags=tags,
        row_classes=row_classes,
        filters=_facets(rows, classes, row_classes, tags, frozen_objects),
        geometry="rle" if rles else ("boxes" if frozen_objects else None),
        image_id_present=any(row.image_id is not None for row in rows),
        metadata_keys=tuple(
            key for key in _ROW_METADATA if any(row_metadata(row)[key] is not None for row in rows)
        ),
        row_index={row.row: row for row in rows},
    )


def _group_text(records: Iterable[sqlite3.Row], column: str) -> dict[int, tuple[str, ...]]:
    """Group a two-column `(row, value)` table by row.

    Parameters
    ----------
    records : sequence of sqlite3.Row
        The already-ordered records.
    column : str
        The name of the value column.

    Returns
    -------
    dict of int to tuple of str
        One tuple per row, in the order the query returned.
    """
    grouped: dict[int, list[str]] = defaultdict(list)
    for record in records:
        grouped[record["row"]].append(record[column])
    return {key: tuple(value) for key, value in grouped.items()}


def _group_by_object(
    records: Iterable[sqlite3.Row], column: str
) -> dict[tuple[int, int], tuple[str, ...]]:
    """Group a three-column `(row, idx, value)` table by the object it annotates.

    Parameters
    ----------
    records : sequence of sqlite3.Row
        The already-ordered records.
    column : str
        The name of the value column.

    Returns
    -------
    dict of tuple to tuple of str
        One tuple per `(row, idx)`, in the order the query returned.
    """
    grouped: dict[tuple[int, int], list[str]] = defaultdict(list)
    for record in records:
        grouped[(record["row"], record["idx"])].append(record[column])
    return {key: tuple(value) for key, value in grouped.items()}


def _group_classes(
    records: Iterable[sqlite3.Row], classes: Sequence[ClassInfo], path: Path
) -> dict[int, tuple[str, ...]]:
    """Resolve `row_classes` ids to palette names, grouped by row.

    Parameters
    ----------
    records : sequence of sqlite3.Row
        The already-ordered `(row, class_id)` records.
    classes : sequence of ClassInfo
        The dataset's palette.
    path : pathlib.Path
        The file's path, so a refusal can name what is wrong.

    Returns
    -------
    dict of int to tuple of str
        One tuple of class names per row.

    Raises
    ------
    DatasetIndexError
        A row names a class id the palette does not define. Dropping it silently would publish a
        mask whose legend is wrong.
    """
    names = {class_info.id: class_info.name for class_info in classes}
    grouped: dict[int, list[str]] = defaultdict(list)
    for record in records:
        class_id = record["class_id"]
        if class_id not in names:
            raise DatasetIndexError(
                f"{path}: row {record['row']} names class {class_id},"
                " which the palette does not define"
            )
        grouped[record["row"]].append(names[class_id])
    return {key: tuple(value) for key, value in grouped.items()}


def _facets(
    rows: Sequence[ImageRow],
    classes: Sequence[ClassInfo],
    row_classes: Mapping[int, tuple[str, ...]],
    tags: Mapping[int, tuple[str, ...]],
    objects: Mapping[int, tuple[ImageObject, ...]],
) -> dict[str, dict[str, int]]:
    """Compute every facet the index can answer, once.

    Parameters
    ----------
    rows : sequence of ImageRow
        Every image.
    classes : sequence of ClassInfo
        The palette, empty when the dataset has none.
    row_classes : mapping of int to tuple of str
        Class names present per row.
    tags : mapping of int to tuple of str
        Tags per row.
    objects : mapping of int to tuple of ImageObject
        Objects per row.

    Returns
    -------
    dict of str to dict of str to int
        Filter key to value to row count, in the order a description lists them: `split` always,
        then `class`, `tags`, `category`, `attribute`, then the row metadata fields the index
        fills. A key is present only when the index holds a value for it, which is what makes a
        dataset's filter keys a fact about the artifact rather than configuration in code.
    """
    split_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        split_counts[row.split] += 1
    filters: dict[str, dict[str, int]] = {"split": dict(sorted(split_counts.items()))}

    if classes:
        class_counts: dict[str, int] = defaultdict(int)
        for names in row_classes.values():
            for name in names:
                class_counts[name] += 1
        order = {class_info.name: index for index, class_info in enumerate(classes)}
        filters["class"] = dict(sorted(class_counts.items(), key=lambda item: order[item[0]]))

    if tags:
        tag_rows: dict[str, set[int]] = defaultdict(set)
        for row_number, values in tags.items():
            for value in values:
                tag_rows[value].add(row_number)
        filters["tags"] = {value: len(seen) for value, seen in sorted(tag_rows.items())}

    if objects:
        category_rows: dict[str, set[int]] = defaultdict(set)
        attribute_rows: dict[str, set[int]] = defaultdict(set)
        for row_number, items in objects.items():
            for item in items:
                category_rows[item.category].add(row_number)
                for attribute in item.attributes:
                    attribute_rows[attribute].add(row_number)
        filters["category"] = {value: len(seen) for value, seen in sorted(category_rows.items())}
        if attribute_rows:
            filters["attribute"] = {
                value: len(seen) for value, seen in sorted(attribute_rows.items())
            }

    for key in _ROW_METADATA:
        field_counts: dict[str, int] = defaultdict(int)
        for row in rows:
            field_value = row_metadata(row)[key]
            if field_value is not None:
                field_counts[field_value] += 1
        if field_counts:
            filters[key] = dict(sorted(field_counts.items()))

    return filters
