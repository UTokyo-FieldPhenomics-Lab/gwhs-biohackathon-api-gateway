"""What the publisher knows about image bytes, without decoding them.

The upstream is the only encoder that ever runs, so the publisher copies bytes and reads
their magic only to name the extension and the content type. Nothing here imports Pillow.
"""

from __future__ import annotations

JPEG_MAGIC = b"\xff\xd8\xff"
"""First three bytes of every JPEG, and the whole test for one."""

PNG_MAGIC = b"\x89PNG"
"""PNG's first four bytes; the full eight-byte signature's tail adds nothing."""

EXTENSIONS = ("jpg", "png")
"""Extensions the publisher will put in an object name."""

CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png"}
"""The type served with each extension. S3 stores no type of its own."""

CACHE_CONTROL = "public, max-age=31536000, immutable"
"""Every object's cache lifetime: a path is written once, so a viewer never revalidates."""


class UnknownImageFormat(ValueError):
    """The leading bytes match neither JPEG nor PNG."""


def sniff_image_ext(data: bytes) -> str:
    """Name the encoding of an image from its leading bytes.

    Parameters
    ----------
    data : bytes
        The object body, or at least its first few bytes.

    Returns
    -------
    str
        `"jpg"` or `"png"`.

    Raises
    ------
    UnknownImageFormat
        The magic matches neither format. The caller names the row; guessing an extension
        would publish an object whose type no browser and no client can trust.
    """
    if data.startswith(JPEG_MAGIC):
        return "jpg"
    if data.startswith(PNG_MAGIC):
        return "png"
    magic = data[:4].hex()
    raise UnknownImageFormat(
        f"image magic {magic!r} is neither JPEG ({JPEG_MAGIC.hex()}) nor PNG ({PNG_MAGIC.hex()})"
    )
