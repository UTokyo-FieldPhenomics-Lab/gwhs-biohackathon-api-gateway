"""What every upstream parquet reader shares.

A reader's job is to turn one schema into neutral rows, and the schemas disagree about almost
everything except the ground around them: how an upstream split is named, how an object's bytes are
read out of a record, and how their encoding and dimensions are named. Keeping those here is what
makes a new reader a row mapper rather than a fourth copy of the same code.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from biohack_datasets.formats import UnknownImageFormat, sniff_image_ext

BATCH_SIZE = 8
"""Rows decoded at a time. Images make a default batch of 64k rows unbounded."""


class AdapterError(RuntimeError):
    """An upstream file does not match its schema; the row is named."""


def split_of(path: Path) -> str:
    """Read an upstream split name from a parquet file name.

    Parameters
    ----------
    path : pathlib.Path
        A file such as `data/train-00000-of-00001.parquet`.

    Returns
    -------
    str
        The part before the first dash, `train` in the example.
    """
    return path.name.split("-", 1)[0]


def object_bytes(record: dict[str, Any], column: str, *, where: str) -> bytes:
    """Return one image-shaped column of a record, verbatim.

    Parameters
    ----------
    record : dict
        The decoded row.
    column : str
        Column holding a Hugging Face image struct, such as `image` or `mask`.
    where : str
        Prefix naming the file and row, used to name a failure.

    Returns
    -------
    bytes
        The object exactly as the upstream stored it.

    Raises
    ------
    AdapterError
        The column is absent, is not a struct, or holds no bytes.
    """
    value = record.get(column)
    body = value.get("bytes") if isinstance(value, dict) else None
    if not isinstance(body, bytes) or not body:
        raise AdapterError(f"{where}: no {column} bytes")
    return body


def image_extension(body: bytes, *, where: str, what: str = "image") -> str:
    """Name an image's encoding, adding the row to a refusal.

    Parameters
    ----------
    body : bytes
        The object's bytes, or at least its leading ones.
    where : str
        Prefix naming the file and row.
    what : str, optional
        Which object the bytes are, `image` or `mask`: the magic alone cannot say.

    Returns
    -------
    str
        `jpg` or `png`.

    Raises
    ------
    UnknownImageFormat
        The magic matches neither format.
    """
    try:
        return sniff_image_ext(body)
    except UnknownImageFormat as error:
        raise UnknownImageFormat(f"{where}: {what}: {error}") from error


def image_dimensions(body: bytes, *, where: str, what: str = "image") -> tuple[int, int]:
    """Read an image's pixel dimensions from its header, without decoding it.

    The upstream that publishes no `width` and `height` columns is the reason this exists. The
    header is read and the pixels are not: the bytes are copied verbatim into the object, so
    nothing here may re-encode an image.

    Parameters
    ----------
    body : bytes
        The object's bytes, exactly as the upstream stored them.
    where : str
        Prefix naming the file and row, used to name a failure.
    what : str, optional
        Which object the bytes are, `image` or `mask`, used the same way.

    Returns
    -------
    tuple of (int, int)
        Width and height in pixels.

    Raises
    ------
    AdapterError
        The bytes are not an image Pillow can open, so no dimensions can be trusted.
    """
    try:
        with Image.open(BytesIO(body)) as opened:
            width, height = opened.size
    except OSError as error:
        raise AdapterError(f"{where}: {what} is not a readable image: {error}") from error
    return int(width), int(height)
