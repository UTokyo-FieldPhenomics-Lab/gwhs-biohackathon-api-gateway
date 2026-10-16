"""The dataset surface: the publisher's index answered over HTTP.

Read-only and index-backed. Every answer is a lookup in memory, no bytes cross the gateway, and a
client-supplied address is parsed into `(dataset, row)` and resolved through that lookup rather than
concatenated into a path. The contract lives in the Pydantic models here, so the generated reference
is a projection of this module and the frozen surface has one definition.

A field is present when the index holds one and absent when it does not, which is what
`response_model_exclude_unset=True` buys: the `mask_url` a box dataset has as `None` stays
visible, while a `classes` key nothing could fill is simply not there. The alternative — one model
per dataset shape — would be three parallel definitions of the same body.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from biohack_gateway.dataset_index import Dataset, ImageObject, ImageRow

DEFAULT_DATA_BASE_URL = "https://data.global-wheat.com"
"""The public distribution the image addresses point at, until the host names another."""

DEFAULT_PAGE_SIZE = 100
"""Rows a list returns when the client asks for no particular number."""

MAX_PAGE_SIZE = 1000
"""The largest page the surface will build. There is no dataset-dump endpoint."""


class ClassEntry(BaseModel):
    """One class of a dataset's palette, as the description publishes it.

    Attributes
    ----------
    id : int
        The upstream class id, and the value a label image uses plus one.
    name : str
        The class name.
    color : str
        The palette colour, `#rrggbb`.
    """

    id: int
    name: str
    color: str


class MaskInfo(BaseModel):
    """What a dataset's mask object is, where it has one.

    Attributes
    ----------
    kind : str
        `semantic` or `instance`, from the dataset's own index row.
    """

    kind: str


class DatasetSummary(BaseModel):
    """One line of the directory.

    Attributes
    ----------
    id : str
        The dataset id.
    rows : int
        How many images the dataset holds.
    splits : dict of str to int
        Rows per split.
    geometry : str or None
        `rle` or `boxes` where objects carry geometry, `None` where they do not.
    mask : MaskInfo or None
        The mask object, where the dataset ships one.
    """

    id: str
    rows: int
    splits: dict[str, int]
    geometry: str | None
    mask: MaskInfo | None


class DatasetDirectory(BaseModel):
    """The `GET /datasets` body.

    Attributes
    ----------
    datasets : list of DatasetSummary
        One entry per published dataset, ordered by id.
    """

    datasets: list[DatasetSummary]


class DatasetDescription(BaseModel):
    """One dataset's own contract, fetched once per session.

    Attributes
    ----------
    dataset : str
        The dataset id.
    revision : str
        The pinned upstream revision the index was built from.
    rules : str
        The installed publisher version that built it.
    license : str or None
        The license recorded in `sources.yaml`, `None` until it is cleared.
    rows : int
        How many images the dataset holds.
    generated_at : str
        When the publisher built the index.
    splits : dict of str to int
        Rows per split.
    classes : list of ClassEntry or None
        The mask palette, where the dataset has one. It is returned here and nowhere else, so a
        client fetches it once rather than reading it off every row.
    filters : dict of str to dict of str to int
        Every filter key the dataset offers, with a row count per value.
    """

    dataset: str
    revision: str
    rules: str
    license: str | None
    rows: int
    generated_at: str
    splits: dict[str, int]
    classes: list[ClassEntry] | None = None
    filters: dict[str, dict[str, int]]


class Statistics(BaseModel):
    """What a list page says about one row without returning its annotation.

    Attributes
    ----------
    objects : int or None
        How many objects the row carries, where it carries any.
    tags : int or None
        How many tags the row carries, where it carries any.
    classes : list of str or None
        The classes the row's mask has, where the dataset is semantic.
    """

    objects: int | None = None
    tags: int | None = None
    classes: list[str] | None = None


class ImageSummary(BaseModel):
    """One row of a list page.

    Attributes
    ----------
    row : int
        The absolute row of the pinned revision, and half of the address.
    split : str
        The split the row belongs to.
    width : int
        The image's width in pixels.
    height : int
        The image's height in pixels.
    image_url : str
        The public `data.*` URL of the image object.
    mask_url : str or None
        The public URL of the mask object, where the dataset ships one.
    image_id : str or None
        The upstream's own id, where it has one. Carried, never an address.
    domain : str or None
        The row's domain, where the dataset carries one.
    country : str or None
        The row's country, where the dataset carries one.
    location : str or None
        The row's location, where the dataset carries one.
    development_stage : str or None
        The row's development stage, where the dataset carries one.
    statistics : Statistics
        Counts and presence sets, never the annotation.
    """

    row: int
    split: str
    width: int
    height: int
    image_url: str
    mask_url: str | None
    image_id: str | None = None
    domain: str | None = None
    country: str | None = None
    location: str | None = None
    development_stage: str | None = None
    statistics: Statistics


class ImageList(BaseModel):
    """The `GET /datasets/{dataset}/images` body.

    Attributes
    ----------
    dataset : str
        The dataset id.
    count : int
        How many rows this page holds.
    next_cursor : str or None
        The opaque cursor for the page after this one, or `None` at the end.
    images : list of ImageSummary
        The page, in row order.
    """

    dataset: str
    count: int
    next_cursor: str | None
    images: list[ImageSummary]


class RleMask(BaseModel):
    """One object's COCO run-length mask, verbatim from the upstream.

    Attributes
    ----------
    size : list of int
        `[height, width]`, as the upstream published it.
    counts : str
        The compressed string. The gateway never encodes or decodes one.
    """

    size: list[int]
    counts: str


class ObjectOut(BaseModel):
    """One annotated object of a row.

    Attributes
    ----------
    idx : int
        The object's index inside its row, dense from zero.
    category : str
        The resolved class name, or the upstream integer as text where it named none.
    bbox : list of float
        `[x, y, w, h]`.
    area : float or None
        The upstream's own area, where it published one. Never computed here.
    attributes : list of str or None
        The upstream's attribute names, where the object has any.
    rle : RleMask or None
        The object's geometry, where the dataset publishes it as RLE.
    score : float or None
        How sure a model was, where the object came from one. Ground truth leaves it unset, which is
        what keeps it out of a dataset response — the surface serializes with
        `response_model_exclude_unset=True` — and it is declared here rather than in the model
        surface because `gateway.md` fixes **one** object shape across the two surfaces.
    """

    idx: int
    category: str
    bbox: list[float]
    area: float | None = None
    attributes: list[str] | None = None
    rle: RleMask | None = None
    score: float | None = None


class ImageDetail(BaseModel):
    """The `GET /datasets/{dataset}/images/{row}` body: one row and its whole annotation.

    Attributes
    ----------
    dataset : str
        The dataset id.
    row : int
        The absolute row of the pinned revision.
    split : str
        The split the row belongs to.
    width : int
        The image's width in pixels.
    height : int
        The image's height in pixels.
    image_url : str
        The public `data.*` URL of the image object.
    mask_url : str or None
        The public URL of the mask object, where the dataset ships one.
    image_id : str or None
        The upstream's own id, where it has one.
    domain : str or None
        The row's domain, where the dataset carries one.
    country : str or None
        The row's country, where the dataset carries one.
    location : str or None
        The row's location, where the dataset carries one.
    development_stage : str or None
        The row's development stage, where the dataset carries one.
    tags : list of str or None
        The row's tags, where it has any.
    objects : list of ObjectOut or None
        The row's geometry, where the dataset carries objects.
    """

    dataset: str
    row: int
    split: str
    width: int
    height: int
    image_url: str
    mask_url: str | None
    image_id: str | None = None
    domain: str | None = None
    country: str | None = None
    location: str | None = None
    development_stage: str | None = None
    tags: list[str] | None = None
    objects: list[ObjectOut] | None = None


def create_dataset_router(datasets: Mapping[str, Dataset], data_base_url: str) -> APIRouter:
    """Build the dataset surface over a loaded index.

    Parameters
    ----------
    datasets : mapping of str to Dataset
        The index, loaded at startup. It is read-only and lives for the process, so a republish
        arrives as a restart rather than as a query.
    data_base_url : str
        The public distribution's base URL, with or without a trailing slash.

    Returns
    -------
    APIRouter
        The routes, ready to include. They inherit the request gate from the application, so
        nothing here authenticates and nothing here is anonymous.
    """
    base = data_base_url.rstrip("/")
    router = APIRouter(tags=["datasets"])

    @router.get(
        "/datasets",
        response_model=DatasetDirectory,
        summary="The dataset directory",
        description="One line per dataset: size, splits, and the shape of its annotation.",
    )
    def list_datasets() -> DatasetDirectory:
        """List every dataset the gateway loaded.

        Returns
        -------
        DatasetDirectory
            One summary per dataset, in id order.
        """
        return DatasetDirectory(
            datasets=[_directory_entry(dataset) for dataset in datasets.values()]
        )

    @router.get(
        "/datasets/{dataset}",
        response_model=DatasetDescription,
        response_model_exclude_unset=True,
        summary="One dataset's contract",
        description="Provenance, splits, palette, and filter keys. Fetched once per session.",
    )
    def describe_dataset(dataset: str) -> DatasetDescription:
        """Describe one dataset.

        Parameters
        ----------
        dataset : str
            The dataset id from the path.

        Returns
        -------
        DatasetDescription
            The dataset's own contract, with its palette where it has one.

        Raises
        ------
        HTTPException
            No dataset has that id.
        """
        return _dataset_description(_require(datasets, dataset))

    @router.get(
        "/datasets/{dataset}/images",
        response_model=ImageList,
        response_model_exclude_unset=True,
        summary="List images",
        description="Rows, addresses, and statistics. The annotation is one row at a time.",
    )
    def list_images(
        dataset: str,
        request: Request,
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: str | None = None,
    ) -> ImageList:
        """List the rows a filter selects.

        Parameters
        ----------
        dataset : str
            The dataset id from the path.
        request : Request
            The incoming request, where the filter pairs are read from.
        limit : int, optional
            The page's ceiling.
        cursor : str or None, optional
            The opaque position a previous page handed on.

        Returns
        -------
        ImageList
            The page, with a cursor when more rows follow.

        Raises
        ------
        HTTPException
            No dataset has that id, or the cursor is not one this surface produced.
        """
        found = _require(datasets, dataset)
        offset = _cursor_offset(cursor)
        filters = _filter_pairs(request.query_params.multi_items())
        page = found.select(filters, offset=offset, limit=limit)
        return ImageList(
            dataset=found.id,
            count=len(page.rows),
            next_cursor=_encode_cursor(page.next_offset),
            images=[_row_summary(found, row, base) for row in page.rows],
        )

    @router.get(
        "/datasets/{dataset}/images/{row}",
        response_model=ImageDetail,
        response_model_exclude_unset=True,
        summary="One image's annotation",
        description="The row's geometry, returned on demand. A mask is an object, not a body here.",
    )
    def image_detail(dataset: str, row: int) -> ImageDetail:
        """Return one row's whole annotation.

        Parameters
        ----------
        dataset : str
            The dataset id from the path.
        row : int
            The absolute row of the pinned revision.

        Returns
        -------
        ImageDetail
            The row, its metadata, and its geometry where the dataset carries any.

        Raises
        ------
        HTTPException
            No dataset has that id, or the dataset has no such row.
        """
        found = _require(datasets, dataset)
        image = found.row_index.get(row)
        if image is None:
            raise HTTPException(status_code=404, detail="no such row")
        return _row_detail(found, image, base)

    return router


def _require(datasets: Mapping[str, Dataset], dataset: str) -> Dataset:
    """Resolve a dataset id through the index, or refuse.

    Parameters
    ----------
    datasets : mapping of str to Dataset
        The loaded index.
    dataset : str
        The id from the path. It is a lookup key and never a path fragment.

    Returns
    -------
    Dataset
        The dataset.

    Raises
    ------
    HTTPException
        No dataset has that id. The name is not repeated in the message, so nothing a caller sent
        is echoed back.
    """
    found = datasets.get(dataset)
    if found is None:
        raise HTTPException(status_code=404, detail="no such dataset")
    return found


def _object_url(base: str, dataset: str, kind: str, row: int, extension: str) -> str:
    """Build the public URL of one object.

    Parameters
    ----------
    base : str
        The distribution's base URL, without a trailing slash.
    dataset : str
        The dataset id, which is the object's top-level prefix. It is the loaded index's own id,
        never the request's text.
    kind : str
        `images` or `masks`.
    row : int
        The absolute row of the pinned revision.
    extension : str
        The per-row extension the index stored.

    Returns
    -------
    str
        The URL a client fetches.
    """
    return f"{base}/{dataset}/{kind}/{row:08d}.{extension}"


def image_addresses(dataset: Dataset, row: ImageRow, base: str) -> dict[str, Any]:
    """Build the two object addresses every row response carries.

    Parameters
    ----------
    dataset : Dataset
        The dataset the row belongs to.
    row : ImageRow
        The row.
    base : str
        The distribution's base URL.

    Returns
    -------
    dict of str to Any
        `image_url`, and `mask_url` where the row has a mask.

    Notes
    -----
    Public, and used by both surfaces: the model surface resolves a predict's address through this
    function, so a row is addressed one way rather than two. The key layout it builds is a fact of
    the published distribution, and a second copy of it is a second thing to keep true.
    """
    return {
        "image_url": _object_url(base, dataset.id, "images", row.row, row.image_ext),
        "mask_url": (
            _object_url(base, dataset.id, "masks", row.row, row.mask_ext)
            if row.mask_ext is not None
            else None
        ),
    }


def _extras(dataset: Dataset, row: ImageRow) -> dict[str, Any]:
    """Build the fields a dataset either has or does not.

    Parameters
    ----------
    dataset : Dataset
        The dataset the row belongs to.
    row : ImageRow
        The row.

    Returns
    -------
    dict of str to Any
        The upstream id where the dataset carries one, and each metadata field the dataset fills.
    """
    payload: dict[str, Any] = {}
    if dataset.image_id_present and row.image_id is not None:
        payload["image_id"] = row.image_id
    for key in dataset.metadata_keys:
        payload[key] = getattr(row, key)
    return payload


def _directory_entry(dataset: Dataset) -> DatasetSummary:
    """Build one line of the directory.

    Parameters
    ----------
    dataset : Dataset
        The dataset to summarize.

    Returns
    -------
    DatasetSummary
        The dataset's size, splits, and annotation shape.
    """
    return DatasetSummary(
        id=dataset.id,
        rows=len(dataset.rows),
        splits=dict(dataset.filters.get("split", {})),
        geometry=dataset.geometry,
        mask=MaskInfo(kind=dataset.mask_kind) if dataset.mask_kind is not None else None,
    )


def _dataset_description(dataset: Dataset) -> DatasetDescription:
    """Build one dataset's own contract.

    Parameters
    ----------
    dataset : Dataset
        The dataset to describe.

    Returns
    -------
    DatasetDescription
        The provenance, splits, and facets, with the palette where the dataset has one.
    """
    payload: dict[str, Any] = {
        "dataset": dataset.id,
        "revision": dataset.revision,
        "rules": dataset.rules,
        "license": dataset.license,
        "rows": len(dataset.rows),
        "generated_at": dataset.generated_at,
        "splits": dict(dataset.filters.get("split", {})),
        "filters": {key: dict(value) for key, value in dataset.filters.items()},
    }
    if dataset.classes:
        payload["classes"] = [
            ClassEntry(id=class_info.id, name=class_info.name, color=class_info.color)
            for class_info in dataset.classes
        ]
    return DatasetDescription(**payload)


def _statistics(dataset: Dataset, row: ImageRow) -> Statistics:
    """Count what one row carries, without returning the annotation.

    Parameters
    ----------
    dataset : Dataset
        The dataset the row belongs to.
    row : ImageRow
        The row.

    Returns
    -------
    Statistics
        The counts and class presence the dataset holds for that row.
    """
    payload: dict[str, Any] = {}
    objects = dataset.objects.get(row.row, ())
    if objects:
        payload["objects"] = len(objects)
    tags = dataset.tags.get(row.row, ())
    if tags:
        payload["tags"] = len(tags)
    classes = dataset.row_classes.get(row.row, ())
    if classes:
        payload["classes"] = list(classes)
    return Statistics(**payload)


def _row_summary(dataset: Dataset, row: ImageRow, base: str) -> ImageSummary:
    """Build one list entry.

    Parameters
    ----------
    dataset : Dataset
        The dataset the row belongs to.
    row : ImageRow
        The row.
    base : str
        The distribution's base URL.

    Returns
    -------
    ImageSummary
        Identification, addresses, and statistics — never the annotation.
    """
    payload: dict[str, Any] = {
        "row": row.row,
        "split": row.split,
        "width": row.width,
        "height": row.height,
        **image_addresses(dataset, row, base),
        "statistics": _statistics(dataset, row),
    }
    payload.update(_extras(dataset, row))
    return ImageSummary(**payload)


def _row_detail(dataset: Dataset, row: ImageRow, base: str) -> ImageDetail:
    """Build one row's annotation response.

    Parameters
    ----------
    dataset : Dataset
        The dataset the row belongs to.
    row : ImageRow
        The row.
    base : str
        The distribution's base URL.

    Returns
    -------
    ImageDetail
        The row, its metadata, its tags, and its objects where the dataset carries any.
    """
    payload: dict[str, Any] = {
        "dataset": dataset.id,
        "row": row.row,
        "split": row.split,
        "width": row.width,
        "height": row.height,
        **image_addresses(dataset, row, base),
    }
    payload.update(_extras(dataset, row))
    tags = dataset.tags.get(row.row, ())
    if tags:
        payload["tags"] = list(tags)
    objects = dataset.objects.get(row.row, ())
    if objects:
        payload["objects"] = [_object_entry(item) for item in objects]
    return ImageDetail(**payload)


def _object_entry(item: ImageObject) -> ObjectOut:
    """Build one object, with only the parts the index holds.

    Parameters
    ----------
    item : ImageObject
        The object.

    Returns
    -------
    ObjectOut
        Box and category always; area, attributes, and RLE where they exist.
    """
    payload: dict[str, Any] = {
        "idx": item.idx,
        "category": item.category,
        "bbox": list(item.bbox),
    }
    if item.area is not None:
        payload["area"] = item.area
    if item.attributes:
        payload["attributes"] = list(item.attributes)
    if item.rle is not None:
        payload["rle"] = RleMask(size=[item.rle.height, item.rle.width], counts=item.rle.counts)
    return ObjectOut(**payload)


def _encode_cursor(offset: int | None) -> str | None:
    """Encode the position a following page starts at.

    Parameters
    ----------
    offset : int or None
        The offset, or `None` when this page is the last.

    Returns
    -------
    str or None
        The opaque cursor, or `None`.
    """
    if offset is None:
        return None
    document = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(document).decode()


def _cursor_offset(cursor: str | None) -> int:
    """Read a cursor back into an offset.

    Parameters
    ----------
    cursor : str or None
        The opaque value the client presented, or nothing for the first page.

    Returns
    -------
    int
        The offset to start the page at.

    Raises
    ------
    HTTPException
        The value is not a cursor this surface produced. It is refused rather than ignored,
        because silently starting over answers a question the client did not ask.
    """
    if cursor is None:
        return 0
    try:
        document = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="the cursor is not valid") from None
    offset = document.get("o") if isinstance(document, dict) else None
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise HTTPException(status_code=400, detail="the cursor is not valid")
    return offset


def _filter_pairs(pairs: Sequence[tuple[str, str]]) -> Sequence[tuple[str, str]]:
    """Keep only the pairs that are filters, not paging.

    Parameters
    ----------
    pairs : sequence of tuple
        The query pairs.

    Returns
    -------
    sequence of tuple
        The pairs a dataset answers, in the order they arrived.
    """
    return [(key, value) for key, value in pairs if key not in ("limit", "cursor")]
