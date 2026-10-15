"""Tests for naming an image's encoding from its leading bytes."""

from __future__ import annotations

import pytest

from biohack_datasets.formats import UnknownImageFormat, sniff_image_ext


def test_jpeg_magic_names_jpg() -> None:
    """The three-byte JPEG marker is the whole test."""
    assert sniff_image_ext(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00") == "jpg"


def test_png_magic_names_png() -> None:
    """PNG's first four bytes are enough; the signature tail adds nothing."""
    assert sniff_image_ext(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR") == "png"


def test_unknown_magic_stops_and_names_the_bytes() -> None:
    """A format we cannot name must not be guessed into an extension."""
    with pytest.raises(UnknownImageFormat, match="47494638"):
        sniff_image_ext(b"GIF89a")
