"""Adapter for the GWFSS schema: an image and the semantic mask that annotates it.

A row's annotation is a second image whose pixel colours *are* the classes, so the palette is
neither optional nor derivable: it is the pinned vocabulary, injected into the reader. The mask is
carried verbatim, and the classes it contains are read from its own pixels rather than repainted.

The schema publishes no `width` and `height`, so both headers are read, and a mask whose size
differs from its image is refused: the annotation would not lie over the photo, and every class
recorded for that row would be recorded for the wrong pixels.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from PIL import Image

from biohack_datasets.adapters.common import (
    BATCH_SIZE,
    AdapterError,
    image_dimensions,
    image_extension,
    object_bytes,
    split_of,
)
from biohack_datasets.labels import Palette, UnknownMaskColour
from biohack_datasets.model import Row


class GwfssAdapter:
    """Reads the GWFSS parquet schema into neutral rows.

    Parameters
    ----------
    palette : Palette
        The pinned class vocabulary the mask's colours index into. It is required because the row's
        classes cannot be named without it.
    """

    mask_kind: str | None = "semantic"
    """GWFSS ships one semantic mask image per row."""

    def __init__(self, palette: Palette) -> None:
        self._palette = palette

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
            The next image and its mask.

        Raises
        ------
        AdapterError
            A row has no image or no mask, the two disagree about size, or the mask paints a colour
            the palette does not define.
        UnknownImageFormat
            An image's magic is neither JPEG nor PNG; the row is named in the message.
        """
        row_number = 0
        for path in files:
            split = split_of(path)
            parquet = pq.ParquetFile(path)
            for batch in parquet.iter_batches(batch_size=BATCH_SIZE):
                for record in batch.to_pylist():
                    yield _row_from(record, row_number, split, self._palette, path)
                    row_number += 1


def _row_from(
    record: dict[str, Any], row_number: int, split: str, palette: Palette, path: Path
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
    palette : Palette
        Names the classes the mask's colours paint.
    path : pathlib.Path
        File the record came from, used to name a failure.

    Returns
    -------
    Row
        The neutral row.

    Raises
    ------
    AdapterError
        Image or mask bytes are missing, the two disagree about size, or the mask paints outside
        the palette.
    UnknownImageFormat
        An image's magic is neither JPEG nor PNG.
    """
    where = f"{path}: row {row_number}"
    body = object_bytes(record, "image", where=where)
    mask = object_bytes(record, "mask", where=where)
    extension = image_extension(body, where=where)
    mask_extension = image_extension(mask, where=where, what="mask")
    width, height = image_dimensions(body, where=where)
    mask_width, mask_height = image_dimensions(mask, where=where, what="mask")
    if (mask_width, mask_height) != (width, height):
        raise AdapterError(
            f"{where}: mask is {mask_width}x{mask_height} but the image is {width}x{height}"
        )

    return Row(
        row=row_number,
        split=split,
        image_ext=extension,
        width=width,
        height=height,
        image=body,
        mask_ext=mask_extension,
        mask=mask,
        mask_classes=_mask_classes(mask, palette, where),
    )


def _mask_classes(mask: bytes, palette: Palette, where: str) -> tuple[int, ...]:
    """Return the palette class ids a mask paints.

    Parameters
    ----------
    mask : bytes
        The mask's bytes, exactly as the upstream stored them.
    palette : Palette
        The pinned class vocabulary.
    where : str
        Prefix naming the file and row, used to name a failure.

    Returns
    -------
    tuple of int
        One id per palette colour the mask contains.

    Raises
    ------
    AdapterError
        The bytes are not a readable image, or they paint outside the palette.
    """
    try:
        with Image.open(BytesIO(mask)) as opened:
            return palette.class_ids_in(opened)
    except OSError as error:
        raise AdapterError(f"{where}: mask is not a readable image: {error}") from error
    except UnknownMaskColour as error:
        raise AdapterError(f"{where}: {error}") from error
