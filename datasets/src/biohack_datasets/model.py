"""Neutral rows an adapter reads out of an upstream and the publisher consumes.

An adapter's whole job is to turn one upstream schema into these values: the publisher
never sees a parquet column name, and the index never sees a parquet file. Keeping the
boundary here is what lets the index schema be written once and read by every adapter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rle:
    """One object's COCO run-length encoded mask, verbatim from the upstream.

    Attributes
    ----------
    height : int
        Mask rows, equal to the image's height.
    width : int
        Mask columns, equal to the image's width.
    counts : str
        The upstream's compressed count string. It is copied and never re-encoded: the
        client decodes it, and no codec of ours sits between the two.
    """

    height: int
    width: int
    counts: str


@dataclass(frozen=True)
class Annotation:
    """One object annotation of one image.

    Attributes
    ----------
    idx : int
        Position of the object in its image, dense from zero.
    category : str
        Resolved class name, or the upstream integer as text where the upstream declares
        no vocabulary.
    bbox : tuple of float
        `(x, y, w, h)` in image pixels, verbatim from the upstream.
    area : float or None
        The upstream's own area, or `None` where it publishes none. Never computed here:
        a derived area would disagree with the upstream's RLE.
    attributes : tuple of str
        Resolved per-instance attribute names, orthogonal to `category`.
    rle : Rle or None
        The upstream's run-length mask, where the schema publishes one.
    """

    idx: int
    category: str
    bbox: tuple[float, float, float, float]
    area: float | None = None
    attributes: tuple[str, ...] = ()
    rle: Rle | None = None


@dataclass(frozen=True)
class Row:
    """One image, its optional mask, and its annotation.

    Attributes
    ----------
    row : int
        Absolute row number of the pinned upstream revision: the zero-based cumulative
        index over the dataset's parquet files in sorted path order.
    split : str
        Upstream split, taken from the parquet file name prefix.
    image_ext : str
        Image encoding, sniffed from the leading bytes and lower-cased.
    width, height : int
        Image pixels, copied from the upstream columns where it publishes them.
    image : bytes
        The image exactly as the upstream stored it.
    mask_ext : str or None
        Mask encoding where the dataset ships a mask object, sniffed the same way.
    mask : bytes or None
        The mask exactly as the upstream stored it.
    image_id : str or None
        Upstream image id, carried and never used for addressing.
    domain, country, location, development_stage : str or None
        Upstream filter keys, where the schema has them.
    tags : tuple of str
        Resolved image-level tag names.
    objects : tuple of Annotation
        Object annotations, ordered by `idx`.
    mask_classes : tuple of int
        Palette class ids present in the mask, derived at publish time. Empty when the
        dataset has no mask.
    """

    row: int
    split: str
    image_ext: str
    width: int
    height: int
    image: bytes
    mask_ext: str | None = None
    mask: bytes | None = None
    image_id: str | None = None
    domain: str | None = None
    country: str | None = None
    location: str | None = None
    development_stage: str | None = None
    tags: tuple[str, ...] = ()
    objects: tuple[Annotation, ...] = ()
    mask_classes: tuple[int, ...] = ()
