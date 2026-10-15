"""Adapter for the GWHS schema: an image, tags, and object-level annotations.

The vocabulary is read from the file-level `huggingface` Arrow schema metadata, which is
where a Hugging Face ClassLabel keeps its names. No vocabulary of ours is created and none
is kept in step by hand: the integers in the columns are indexes into that metadata.

Nothing here decodes an image. The extension is sniffed from the leading bytes, the width
and height are copied from the upstream columns, and the object's COCO RLE is carried as the
string the upstream wrote.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from biohack_datasets.adapters.common import (
    BATCH_SIZE,
    AdapterError,
    image_extension,
    object_bytes,
    split_of,
)
from biohack_datasets.model import Annotation, Rle, Row

_HUGGINGFACE_METADATA = b"huggingface"


@dataclass(frozen=True)
class Vocabulary:
    """The ClassLabel name lists of one upstream file.

    Attributes
    ----------
    tags : tuple of str
        Image-level tag names.
    categories : tuple of str
        Object category names.
    attributes : tuple of str
        Per-object attribute names, orthogonal to categories.
    """

    tags: tuple[str, ...]
    categories: tuple[str, ...]
    attributes: tuple[str, ...]


class GwhsAdapter:
    """Reads the GWHS parquet schema into neutral rows."""

    mask_kind: str | None = None
    """GWHS ships its geometry as object-level RLE, not as a mask object."""

    def iter_rows(self, files: Sequence[Path]) -> Iterator[Row]:
        """Yield the rows of every file, numbering them cumulatively from zero.

        Parameters
        ----------
        files : sequence of pathlib.Path
            The dataset's parquet files, already sorted by repository-relative path. The
            order defines the absolute row number and therefore the dataset's addresses.

        Yields
        ------
        Row
            The next image and its annotation.

        Raises
        ------
        AdapterError
            A file carries no ClassLabel metadata, or a row is missing image bytes.
        UnknownImageFormat
            An image's magic is neither JPEG nor PNG; the row is named in the message.
        """
        row_number = 0
        for path in files:
            split = split_of(path)
            parquet = pq.ParquetFile(path)
            vocabulary = _vocabulary_of(parquet.schema_arrow, path)
            for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
                for record in batch.to_pylist():
                    yield _row_from(record, row_number, split, vocabulary, path)
                    row_number += 1


def _vocabulary_of(schema: pa.Schema, path: Path) -> Vocabulary:
    """Extract the ClassLabel name lists from a file's Arrow schema metadata.

    Parameters
    ----------
    schema : pyarrow.Schema
        Schema of the parquet file.
    path : pathlib.Path
        File the schema came from, used to name it on failure.

    Returns
    -------
    Vocabulary
        The names of the tags, object categories, and object attributes.

    Raises
    ------
    AdapterError
        The file-level Hugging Face metadata is absent or shaped unexpectedly. The
        vocabulary is the only place these names exist, so guessing one would be inventing.
    """
    raw = (schema.metadata or {}).get(_HUGGINGFACE_METADATA)
    if raw is None:
        raise AdapterError(
            f"{path}: no file-level huggingface schema metadata, so class names are unknown"
        )
    try:
        features = json.loads(raw)["info"]["features"]
        objects = features["objects"]["feature"]
        return Vocabulary(
            tags=tuple(features["tags"]["feature"]["names"]),
            categories=tuple(objects["category"]["names"]),
            attributes=tuple(objects["attributes"]["feature"]["names"]),
        )
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise AdapterError(f"{path}: unexpected ClassLabel metadata: {error}") from error


def _row_from(
    record: dict[str, Any], row_number: int, split: str, vocabulary: Vocabulary, path: Path
) -> Row:
    """Turn one parquet record into a neutral row.

    Parameters
    ----------
    record : dict
        Decoded row, keyed by parquet column name.
    row_number : int
        Absolute row number across the dataset's files.
    split : str
        Split read from the file name.
    vocabulary : Vocabulary
        Names the integer columns index into.
    path : pathlib.Path
        File the record came from, used to name a failure.

    Returns
    -------
    Row
        The neutral row.

    Raises
    ------
    AdapterError
        Image bytes, width, or height are missing.
    UnknownImageFormat
        The image magic is neither JPEG nor PNG.
    """
    where = f"{path}: row {row_number}"
    body = object_bytes(record, "image", where=where)
    width = record.get("width")
    height = record.get("height")
    if width is None or height is None:
        raise AdapterError(f"{where}: no width or height")
    image_ext = image_extension(body, where=where)

    return Row(
        row=row_number,
        split=split,
        image_ext=image_ext,
        width=int(width),
        height=int(height),
        image=body,
        image_id=record.get("image_id"),
        domain=record.get("domain"),
        country=record.get("country"),
        location=record.get("location"),
        development_stage=record.get("development_stage"),
        tags=tuple(
            _name(vocabulary.tags, index, row_number, "tag") for index in record.get("tags") or ()
        ),
        objects=tuple(
            _annotation(raw, index, row_number, vocabulary)
            for index, raw in enumerate(record.get("objects") or ())
        ),
    )


def _annotation(
    raw: dict[str, Any], index: int, row_number: int, vocabulary: Vocabulary
) -> Annotation:
    """Turn one raw object record into an annotation.

    Parameters
    ----------
    raw : dict
        The object entry as the parquet column decoded it.
    index : int
        Position of the object in its row.
    row_number : int
        Absolute row number, used to name a failure.
    vocabulary : Vocabulary
        Category and attribute names the integer fields index into.

    Returns
    -------
    Annotation
        The annotation, with the upstream's RLE carried verbatim.
    """
    bbox = raw.get("bbox") or (0, 0, 0, 0)
    x, y, w, h = (float(value) for value in bbox)
    rle_raw = raw.get("rle")
    rle = None
    if rle_raw is not None:
        size = rle_raw.get("size") or (0, 0)
        rle = Rle(height=int(size[0]), width=int(size[1]), counts=str(rle_raw.get("counts", "")))
    return Annotation(
        idx=index,
        category=_name(vocabulary.categories, raw.get("category"), row_number, "category"),
        bbox=(x, y, w, h),
        area=None if raw.get("area") is None else float(raw["area"]),
        attributes=tuple(
            _name(vocabulary.attributes, value, row_number, "attribute")
            for value in raw.get("attributes") or ()
        ),
        rle=rle,
    )


def _name(names: tuple[str, ...], index: Any, row_number: int, field: str) -> str:
    """Resolve a class index to its name, or to the index as text without a vocabulary.

    Parameters
    ----------
    names : tuple of str
        The ClassLabel names, empty when the upstream declares none.
    index : Any
        Integer index from the column.
    row_number : int
        Absolute row number, used to name a failure.
    field : str
        Field being resolved, used the same way.

    Returns
    -------
    str
        The resolved name, or the upstream integer as text.

    Raises
    ------
    AdapterError
        The vocabulary exists but does not reach the index.
    """
    if not names:
        return str(index)
    try:
        return names[int(index)]
    except (IndexError, TypeError, ValueError) as error:
        raise AdapterError(
            f"row {row_number}: {field} index {index!r} is outside the upstream vocabulary"
        ) from error
