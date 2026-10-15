"""Adapter for the GWHD schema: an image, four metadata columns, and bounding boxes.

The upstream declares no class vocabulary at all: its `objects.categories` are integers and the
file-level Hugging Face metadata types them as plain `int64`, with no ClassLabel beside them. A
category is therefore stored as the upstream integer in text, and no name is invented.

The schema publishes no `width` and `height` either, so the image's own header is read for them.
That is the only thing this adapter reads out of the pixels, and it decodes nothing: the bytes are
copied into the object exactly as the upstream stored them.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from biohack_datasets.adapters.common import (
    BATCH_SIZE,
    AdapterError,
    image_dimensions,
    image_extension,
    object_bytes,
    split_of,
)
from biohack_datasets.model import Annotation, Row


class GwhdAdapter:
    """Reads the GWHD parquet schema into neutral rows."""

    mask_kind: str | None = None
    """GWHD's annotation is boxes; it ships no mask object."""

    def iter_rows(self, files: Sequence[Path]) -> Iterator[Row]:
        """Yield the rows of every file, numbering them cumulatively from zero.

        Parameters
        ----------
        files : sequence of pathlib.Path
            The dataset's parquet files, already sorted by repository-relative path. The order
            defines the absolute row number and therefore the dataset's addresses.

        Yields
        ------
        Row
            The next image and its boxes.

        Raises
        ------
        AdapterError
            A row has no image bytes, or its boxes and categories do not pair up.
        UnknownImageFormat
            An image's magic is neither JPEG nor PNG; the row is named in the message.
        """
        row_number = 0
        for path in files:
            split = split_of(path)
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
                for record in batch.to_pylist():
                    yield _row_from(record, row_number, split, path)
                    row_number += 1


def _row_from(record: dict[str, Any], row_number: int, split: str, path: Path) -> Row:
    """Turn one parquet record into a neutral row.

    Parameters
    ----------
    record : dict
        Decoded row, keyed by parquet column name.
    row_number : int
        Absolute row number across the dataset's files.
    split : str
        Split read from the file name.
    path : pathlib.Path
        File the record came from, used to name a failure.

    Returns
    -------
    Row
        The neutral row.

    Raises
    ------
    AdapterError
        Image bytes are missing, or the row's boxes and categories do not pair up.
    UnknownImageFormat
        The image magic is neither JPEG nor PNG.
    """
    where = f"{path}: row {row_number}"
    body = object_bytes(record, "image", where=where)
    extension = image_extension(body, where=where)
    width, height = image_dimensions(body, where=where)

    return Row(
        row=row_number,
        split=split,
        image_ext=extension,
        width=width,
        height=height,
        image=body,
        domain=record.get("domain"),
        country=record.get("country"),
        location=record.get("location"),
        development_stage=record.get("development_stage"),
        objects=_annotations(record.get("objects"), where),
    )


def _annotations(objects: Any, where: str) -> tuple[Annotation, ...]:
    """Turn the `objects` struct into annotations.

    Parameters
    ----------
    objects : Any
        The decoded `objects` column: `boxes` and `categories` as two parallel lists.
    where : str
        Prefix naming the file and row, used to name a failure.

    Returns
    -------
    tuple of Annotation
        One annotation per box, numbered densely from zero. No area and no attributes: the schema
        publishes neither, and a derived area would be a number the upstream never stated.

    Raises
    ------
    AdapterError
        The two lists disagree about length, a box is not four numbers, or a category is absent.
    """
    if objects is None:
        return ()
    if not isinstance(objects, dict):
        raise AdapterError(f"{where}: objects is {type(objects).__name__}, not a struct")
    boxes = objects.get("boxes") or []
    categories = objects.get("categories") or []
    if len(boxes) != len(categories):
        raise AdapterError(f"{where}: {len(boxes)} boxes but {len(categories)} categories")
    return tuple(
        Annotation(
            idx=index,
            category=_category(category, where, index),
            bbox=_bbox(box, where),
        )
        for index, (box, category) in enumerate(zip(boxes, categories, strict=True))
    )


def _category(value: Any, where: str, index: int) -> str:
    """Name one object's category, as the upstream integer in text.

    Parameters
    ----------
    value : Any
        The decoded category.
    where : str
        Prefix naming the file and row.
    index : int
        Position of the object in its row.

    Returns
    -------
    str
        The integer as text: the upstream declares no vocabulary, so no name is invented.

    Raises
    ------
    AdapterError
        The category is absent, which would otherwise publish the text `None` as a class name.
    """
    if value is None:
        raise AdapterError(f"{where}: object {index} has no category")
    return str(value)


def _bbox(box: Any, where: str) -> tuple[float, float, float, float]:
    """Read one `[x, y, w, h]` box.

    Parameters
    ----------
    box : Any
        The decoded box.
    where : str
        Prefix naming the file and row, used to name a failure.

    Returns
    -------
    tuple of float
        The box exactly as the upstream published it.

    Raises
    ------
    AdapterError
        The box is not four numbers.
    """
    try:
        x, y, width, height = (float(value) for value in box)
    except (TypeError, ValueError) as error:
        raise AdapterError(f"{where}: a box is not four numbers: {error}") from error
    return (x, y, width, height)
