"""Global Wheat API client: one import, then datasets, models, and masks.

The API is eight HTTP routes; this file is those eight routes as functions, plus the two decoders a
result needs before it is usable. It carries **no response models**: every answer is the API's own
JSON, so this file cannot disagree with the server about a field it does not restate. Only the
routes are named here, and ``clients/routes.json`` beside this file is what keeps them honest
against the gateway's OpenAPI document.

Three things are done for the caller rather than by the caller:

* an address, not a URL — ``image("gwhd", 0)`` resolves ``(dataset, row)`` for you;
* the cursor — ``iter_images`` walks every page so a loop does not have to;
* the geometry — every object comes back with ``mask`` already decoded, a ``(height, width)``
  boolean array, so nothing has to know what COCO's compressed RLE is.

Every answer also keeps the raw ``rle`` entry it came from, which is what makes the decoded mask
checkable: its pixel count equals ``area``.

The file is written to be downloaded and imported, not installed::

    import global_wheat

    client = global_wheat.Client()    # the key comes from GLOBAL_WHEAT_API_KEY
    row = client.image("gwhd", 0)     # a page instead: client.images("gwhd", split="train")
    for obj in row["objects"]:
        print(obj["category"], obj["mask"].sum())

Dependencies are ``requests``, ``numpy`` and ``pillow``, and nothing else.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import numpy as np
import requests
from PIL import Image, ImageOps, UnidentifiedImageError

__all__ = [
    "API_KEY_HEADER",
    "BASE_ENV",
    "DEFAULT_BASE",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_TIMEOUT",
    "KEY_ENV",
    "UPLOAD_QUALITY",
    "ApiError",
    "Client",
    "label_masks",
    "rle_decode",
]

DEFAULT_BASE = "https://api.global-wheat.com"
"""The public gateway. Overridden by the ``base`` argument or by ``GLOBAL_WHEAT_API``."""

BASE_ENV = "GLOBAL_WHEAT_API"
"""The environment variable a host override is read from, so a notebook needs no argument."""

KEY_ENV = "GLOBAL_WHEAT_API_KEY"
"""The environment variable the participant token is read from."""

API_KEY_HEADER = "X-API-Key"
"""Where the token travels. It is sent to the API's own origin and to nothing else."""

DEFAULT_TIMEOUT = 60.0
"""Seconds to wait for an answer, on both sides of a call.

A predict route gives up on a backend after ten seconds and answers ``504``, so this is slack rather
than a budget: an upload from a phone on a slow link is the case it is sized for.
"""

DEFAULT_PAGE_SIZE = 100
"""Rows per page. The API's own default, and its ceiling is 1000."""

UPLOAD_QUALITY = 90
"""The JPEG quality a resized upload is encoded at.

High enough that the resize, not the encoder, is what the model sees; the image is about to be
shrunk again by the model's own preprocessing, and a lower setting buys kilobytes nobody would miss
while adding artifacts to the boundaries a head count depends on.
"""


class ApiError(Exception):
    """One refusal, in the API's own terms.

    Attributes
    ----------
    status : int
        The HTTP status the API answered with.
    code : str
        The machine-readable code from the error body — ``invalid_key``, ``quota_exceeded``,
        ``unavailable``, ``upstream_timeout`` and the rest. A body that is not the contract's shape
        falls back to a code chosen by the status's class.
    message : str
        The human-readable sentence the API wrote. It is safe to print.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class Client:
    """One participant's connection to the API.

    Parameters
    ----------
    key : str or None, optional
        The participant token. ``None`` reads ``GLOBAL_WHEAT_API_KEY``; a client with neither is
        refused here rather than by a ``401`` on the first call.
    base : str or None, optional
        The API's origin. ``None`` reads ``GLOBAL_WHEAT_API``, and then the public host.
    timeout : float, optional
        Seconds to wait for an answer.
    session : requests.Session or None, optional
        A session to send through, where one is already configured. ``None`` makes its own.

    Examples
    --------
    >>> client = Client(key="...")                            # doctest: +SKIP
    >>> client.datasets()[0]["dataset"]                       # doctest: +SKIP
    'gwhd'
    """

    def __init__(
        self,
        key: str | None = None,
        base: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.key = key or os.environ.get(KEY_ENV, "")
        if not self.key:
            raise ValueError(f"no API key: pass key= or set {KEY_ENV} in the environment")
        self.base = (base or os.environ.get(BASE_ENV) or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self._session = session or requests.Session()
        self._session.headers[API_KEY_HEADER] = self.key
        self._descriptions: dict[str, dict[str, Any]] = {}
        self._declarations: dict[str, dict[str, Any] | None] = {}

    def __enter__(self) -> Client:
        """Use the client as a context manager.

        Returns
        -------
        Client
            This client.
        """
        return self

    def __exit__(self, *_: object) -> None:
        """Close the session on the way out."""
        self.close()

    def close(self) -> None:
        """Close the underlying session."""
        self._session.close()

    # -- the dataset surface -------------------------------------------------

    def datasets(self) -> list[dict[str, Any]]:
        """List the datasets this API serves.

        Returns
        -------
        list of dict
            One entry per dataset: its id, row count, splits, and the shape of its annotation.
        """
        body: dict[str, Any] = self._call("GET", "/datasets")
        return cast("list[dict[str, Any]]", body["datasets"])

    def describe(self, dataset: str) -> dict[str, Any]:
        """Describe one dataset: provenance, splits, palette, and filter keys.

        The answer is remembered for the life of the client, because it is a session-level fact and
        the palette is what ``label_masks`` needs on every call.

        Parameters
        ----------
        dataset : str
            The dataset id.

        Returns
        -------
        dict
            The description, with ``classes`` where the dataset has a palette.
        """
        if dataset not in self._descriptions:
            self._descriptions[dataset] = self._call("GET", f"/datasets/{_segment(dataset)}")
        return self._descriptions[dataset]

    def images(
        self,
        dataset: str,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
        **filters: object,
    ) -> dict[str, Any]:
        """Return one page of rows a filter selects.

        Parameters
        ----------
        dataset : str
            The dataset id.
        limit : int, optional
            The page's ceiling, at most 1000.
        cursor : str or None, optional
            The position a previous page handed on. ``None`` starts at the first.
        **filters
            Filter pairs, as ``GET /datasets/{dataset}`` publishes the keys — ``split="train"``,
            ``country="Germany"``. Repeating a key narrows the result rather than widening it, and a
            list is sent as repeated pairs. A key the dataset does not offer selects nothing.

        Returns
        -------
        dict
            ``{"dataset", "count", "next_cursor", "images"}`` — rows and statistics, never the
            annotation itself.
        """
        sent = {name: value for name, value in filters.items() if value is not None}
        params: dict[str, Any] = {"limit": limit, **sent}
        if cursor is not None:
            params["cursor"] = cursor
        body: dict[str, Any] = self._call(
            "GET", f"/datasets/{_segment(dataset)}/images", params=params
        )
        return body

    def iter_images(
        self, dataset: str, *, limit: int = DEFAULT_PAGE_SIZE, **filters: object
    ) -> Iterator[dict[str, Any]]:
        """Walk every row a filter selects, page by page.

        Parameters
        ----------
        dataset : str
            The dataset id.
        limit : int, optional
            Rows per request, which is all this controls: the total is the dataset's own.
        **filters
            The same filter pairs ``images`` takes.

        Yields
        ------
        dict
            One row summary at a time, in row order.

        Examples
        --------
        >>> for row in client.iter_images("gwhd", split="train", limit=200):   # doctest: +SKIP
        ...     print(row["row"], row["statistics"]["objects"])
        """
        cursor: str | None = None
        while True:
            page = self.images(dataset, limit=limit, cursor=cursor, **filters)
            yield from cast("list[dict[str, Any]]", page["images"])
            cursor = cast("str | None", page.get("next_cursor"))
            if not cursor:
                return

    def image(self, dataset: str, row: int, *, decode: bool = True) -> dict[str, Any]:
        """Return one row's whole annotation.

        Parameters
        ----------
        dataset : str
            The dataset id.
        row : int
            The absolute row of the pinned upstream revision.
        decode : bool, optional
            Attach each object's geometry as ``mask``, decoded. ``False`` leaves the answer exactly
            as the API wrote it, which is what a large row with dozens of objects wants.

        Returns
        -------
        dict
            The row, its metadata, and its objects where the dataset carries geometry.

        Raises
        ------
        ApiError
            No dataset has that id, or the dataset has no such row.
        """
        body: dict[str, Any] = self._call("GET", f"/datasets/{_segment(dataset)}/images/{row}")
        if decode:
            _decode_objects(body)
        return body

    def image_bytes(self, dataset: str, row: int) -> bytes:
        """Return the image itself, fetched from the public ``data.*`` plane.

        Parameters
        ----------
        dataset : str
            The dataset id.
        row : int
            The absolute row of the pinned revision.

        Returns
        -------
        bytes
            The image file, exactly as published — JPEG, PNG or TIFF.
        """
        detail = self.image(dataset, row, decode=False)
        return self.fetch(cast("str", detail["image_url"]))

    def mask_bytes(self, dataset: str, row: int) -> bytes:
        """Return the row's mask image, where the dataset ships one.

        Parameters
        ----------
        dataset : str
            The dataset id.
        row : int
            The absolute row of the pinned revision.

        Returns
        -------
        bytes
            The mask file — a palette image for a semantic dataset.

        Raises
        ------
        ValueError
            The dataset publishes no mask for this row.
        """
        detail = self.image(dataset, row, decode=False)
        url = detail.get("mask_url")
        if not url:
            raise ValueError(f"{dataset} row {row} publishes no mask")
        return self.fetch(cast("str", url))

    def fetch(self, url: str) -> bytes:
        """Fetch a public object by the URL a response carried.

        Deliberately a plain request with no key: ``data.*`` is a public distribution, and sending
        the participant token to a second host is how a token leaks into a CDN's logs.

        Parameters
        ----------
        url : str
            An ``image_url`` or ``mask_url`` from a response.

        Returns
        -------
        bytes
            The object.
        """
        response = requests.get(url, timeout=self.timeout)
        if response.status_code >= 400:
            raise ApiError(response.status_code, "not_found", f"no object at {url}")
        return response.content

    # -- the model surface ---------------------------------------------------

    def models(self) -> list[dict[str, Any]]:
        """List the models this API serves.

        Returns
        -------
        list of dict
            One entry per model: the side it runs at, how many machines answered, and the control
            parameters it declares.
        """
        body: dict[str, Any] = self._call("GET", "/models")
        return cast("list[dict[str, Any]]", body["models"])

    def model(self, model: str) -> dict[str, Any]:
        """Describe one model, freshly.

        Parameters
        ----------
        model : str
            The model id.

        Returns
        -------
        dict
            The same entry the directory carries.
        """
        body: dict[str, Any] = self._call("GET", f"/models/{_segment(model)}")
        return body

    def predict(
        self,
        model: str,
        dataset: str,
        row: int,
        *,
        decode: bool = True,
        **params: object,
    ) -> dict[str, Any]:
        """Run one model on one published row.

        Parameters
        ----------
        model : str
            The model id.
        dataset : str
            The dataset id.
        row : int
            The absolute row of the pinned revision. The gateway resolves the address, so no URL is
            ever sent.
        decode : bool, optional
            Attach each object's ``mask``, decoded.
        **params
            The model's own control parameters — ``prompt="wheat head"``,
            ``confidence_threshold=0.5``. ``client.model(id)["parameters"]`` lists them with their
            defaults and bounds; omitting one uses the model's default. A geometry parameter is one
            value: ``boxes=[[480, 290, 110, 360, 1]]`` goes out as one JSON array in one parameter,
            never as five repeated names.

        Returns
        -------
        dict
            The answer as the API wrote it: ``model``, ``objects``, ``resolution_used`` and
            ``computed_by`` always, plus what this model answered with — ``count`` and ``density``
            for a counting model, ``warnings`` where it reported one. ``mask`` is on every object
            and ``density["grid"]`` is the field shaped to its ``size``, both when ``decode`` is
            true; a counting answer has no objects, so only the field is added.

        Raises
        ------
        ApiError
            ``400`` for a request the model refused, ``503`` when no machine is answering, ``504``
            when the model took too long. The first is not worth retrying; the last two are.
        """
        body: dict[str, Any] = self._call(
            "POST",
            f"/models/{_segment(model)}/predict",
            params=_model_params(params),
            json={"dataset": dataset, "row": row},
        )
        if decode:
            _decode_objects(body)
            _decode_density(body)
        return body

    def predict_file(
        self,
        model: str,
        image: str | Path | bytes,
        *,
        resize_before_submit: bool = True,
        decode: bool = True,
        **params: object,
    ) -> dict[str, Any]:
        """Run one model on your own photograph.

        The image is resized to the side the model runs at before it is sent, because the model
        resizes every input it receives anyway: a larger upload buys no resolution, only time on the
        wire. The target is the model's own — ``imgsz`` where you pass it and the model declares it,
        otherwise the ``resolution`` the model publishes — so the pixels the network sees are the
        ones it would have produced from the full image.

        Parameters
        ----------
        model : str
            The model id.
        image : str or pathlib.Path or bytes
            The file to send, as a path or already in memory. JPEG, PNG, WebP, TIFF or GIF, at most
            15 MB and 50 megapixels; the API decides by the bytes rather than by a declared type.
        resize_before_submit : bool, optional
            Resize before sending. ``False`` sends the file untouched, which is the documented
            opt-out for a caller who wants the original bytes on the wire.
        decode : bool, optional
            Attach each object's ``mask``, decoded.
        **params
            The model's own control parameters, encoded as in :meth:`predict` — a geometry value is
            one JSON array in one parameter. ``resize_before_submit`` is this client's own option,
            and is never sent as a parameter.

        Returns
        -------
        dict
            The same body ``predict`` answers.

        Raises
        ------
        ApiError
            ``400`` when the bytes are not an image the API reads, ``413`` over the limit, ``503``
            when no machine is answering, ``504`` on a timeout.
        """
        name, payload, media_type = _upload(image)
        if resize_before_submit:
            target = self._target_side(model, params)
            if target is not None:
                prepared = _prepare_upload(payload, target)
                if prepared is not payload:
                    # The prepared bytes are a JPEG whatever the source was, and a multipart part
                    # that declares something it is not is a lie the API happens to forgive.
                    name, payload, media_type = f"{Path(name).stem}.jpg", prepared, "image/jpeg"
        body: dict[str, Any] = self._call(
            "POST",
            f"/models/{_segment(model)}/predict/upload",
            params=_model_params(params),
            files={"file": (name, payload, media_type)},
        )
        if decode:
            _decode_objects(body)
            _decode_density(body)
        return body

    def _declaration(self, model: str) -> dict[str, Any] | None:
        """Return what a model published about itself, fetching it at most once.

        Parameters
        ----------
        model : str
            The model id.

        Returns
        -------
        dict or None
            The ``GET /models/{id}`` body, or ``None`` where the fetch failed. A failure is cached
            like an answer: the side this client resizes to is a convenience, and asking again on
            every upload would turn one missing declaration into one request per image.

        Notes
        -----
        A failed fetch does not raise. The caller then sends the original bytes, and the predict
        call is what reports the failure — with the status and code the API actually meant.
        """
        if model not in self._declarations:
            try:
                self._declarations[model] = self.model(model)
            except (ApiError, requests.RequestException):
                self._declarations[model] = None
        return self._declarations[model]

    def _target_side(self, model: str, params: Mapping[str, Any]) -> int | None:
        """Work out the side this model would resize the image to anyway.

        Parameters
        ----------
        model : str
            The model id.
        params : mapping
            The parameters the caller passed, which may carry ``imgsz``.

        Returns
        -------
        int or None
            The side to resize to, or ``None`` where the model declared nothing to derive it from
            and the original bytes should travel.

        Notes
        -----
        The number is derived, never invented: a caller's ``imgsz`` is capped by the ceiling the
        model declared, because a larger value would be clamped by the model and resizing to it
        would send pixels nobody uses. Without ``imgsz`` the model's published ``resolution`` is the
        answer, since that is the side its own preprocessing picks.
        """
        declared = self._declaration(model)
        if declared is None:
            return None
        ceiling = _declared_side(declared)
        requested = _as_side(params.get("imgsz"))
        if requested is not None and requested > 0 and ceiling is not None:
            return min(requested, ceiling)
        resolution = declared.get("resolution")
        if isinstance(resolution, int) and resolution > 0:
            return resolution
        return None

    # -- geometry ------------------------------------------------------------

    def label_masks(self, pixels: np.ndarray, dataset: str) -> dict[str, np.ndarray]:
        """Split a palette mask into one boolean mask per class.

        Parameters
        ----------
        pixels : numpy.ndarray
            The mask image as ``(height, width, 3)`` — or ``(height, width, 4)`` as Pillow hands
            back an RGBA image. Read it from the dataset's own mask, which
            ``mask_bytes(dataset, row)`` returns.
        dataset : str
            The dataset whose palette applies. Its description is fetched once and reused.

        Returns
        -------
        dict of str to numpy.ndarray
            One ``(height, width)`` boolean array per class, in palette order.

        Raises
        ------
        ValueError
            The dataset publishes no palette, or a pixel holds a colour outside it.
        """
        classes = self.describe(dataset).get("classes")
        if not classes:
            raise ValueError(f"{dataset} publishes no palette")
        return label_masks(pixels, cast("list[Mapping[str, Any]]", classes))

    # -- transport -----------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        files: Mapping[str, Any] | None = None,
    ) -> Any:
        """Send one request and decode its answer.

        Parameters
        ----------
        method : str
            The HTTP method.
        path : str
            The path from the API's origin, already quoted.
        params : mapping or None, optional
            The query string.
        json : any, optional
            A JSON body.
        files : mapping or None, optional
            A multipart body.

        Returns
        -------
        any
            The decoded JSON body.

        Raises
        ------
        ApiError
            The API refused the request.
        """
        response = self._session.request(
            method,
            f"{self.base}{path}",
            params=params,
            json=json,
            files=files,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise _error(response)
        return response.json()


def _model_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Encode a model's own parameters for the query string.

    Parameters
    ----------
    params : mapping
        The `**params` a caller passed to :meth:`Client.predict` or
        :meth:`Client.predict_file`.

    Returns
    -------
    dict
        The same names with values ready for the transport. A scalar is passed through — the
        transport knows how to write a number — while a list or a mapping becomes **one JSON value**
        in **one** parameter, which is the shape a geometry parameter declares: ``boxes`` is
        ``[[x, y, w, h, label], …]``, not five ``boxes`` parameters. A boolean becomes ``true`` or
        ``false``, because ``True`` is not what the API reads.

    Notes
    -----
    This is only for the model routes. The dataset filters keep the opposite convention on purpose:
    ``?split=train&split=val`` narrows a listing, and that repetition is a different contract with a
    different meaning, so it is not something a client may generalise.
    """
    encoded: dict[str, Any] = {}
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded[name] = "true" if value else "false"
        elif isinstance(value, (list, tuple, dict)):
            encoded[name] = json.dumps(value, separators=(",", ":"))
        else:
            encoded[name] = value
    return encoded


def rle_decode(counts: str, size: Sequence[int]) -> np.ndarray:
    """Decode a COCO compressed RLE into the mask it describes.

    Parameters
    ----------
    counts : str
        The ``rle.counts`` string, verbatim.
    size : sequence of int
        ``(height, width)``, exactly as ``rle.size`` publishes it.

    Returns
    -------
    numpy.ndarray
        A ``(height, width)`` boolean array; ``True`` is the object.

    Raises
    ------
    ValueError
        The counts do not describe a mask of that size.

    Examples
    --------
    >>> mask = rle_decode(obj["rle"]["counts"], obj["rle"]["size"])   # doctest: +SKIP
    >>> int(mask.sum()) == obj["area"]                                # doctest: +SKIP
    True
    """
    runs = _runs(counts)
    height, width = int(size[0]), int(size[1])
    if int(runs.sum()) != height * width:
        raise ValueError(f"the counts describe {int(runs.sum())} pixels, not {height * width}")
    # Only the run boundaries are written and the parity of the boundaries before a pixel is the
    # pixel: one cumulative sum rather than one slice assignment per run.
    boundary = np.zeros(height * width + 1, dtype=np.int8)
    boundary[np.cumsum(runs)] = 1
    flat = np.cumsum(boundary[: height * width]) % 2 == 1
    # COCO flattens column-major, so the flat result is the mask read column by column.
    return cast("np.ndarray", flat.reshape(height, width, order="F"))


def _runs(counts: str) -> np.ndarray:
    """Read a compressed counts string's run lengths.

    Parameters
    ----------
    counts : str
        The string, verbatim.

    Returns
    -------
    numpy.ndarray
        One run length per entry, with COCO's delta encoding applied from the fourth on.
    """
    values: list[int] = []
    value = 0
    shift = 0
    for character in counts:
        chunk = ord(character) - 48
        value |= (chunk & 0x1F) << shift
        shift += 5
        if chunk & 0x20:
            continue
        if chunk & 0x10:  # the last chunk of a negative number is sign-extended
            value |= -1 << shift
        values.append(value + (values[-2] if len(values) > 2 else 0))
        value = 0
        shift = 0
    return np.asarray(values, dtype=np.int64)


def label_masks(image: np.ndarray, classes: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    """Split a palette mask into one boolean array per class.

    Parameters
    ----------
    image : numpy.ndarray
        ``(height, width, 3)`` or ``(height, width, 4)``, as an image decoder hands it back.
    classes : sequence of mapping
        Palette entries with ``name`` and ``color`` (``#rrggbb``) keys, exactly as a dataset's
        description publishes its ``classes``.

    Returns
    -------
    dict of str to numpy.ndarray
        One ``(height, width)`` boolean array per class, in palette order.

    Raises
    ------
    ValueError
        A pixel holds a colour the palette does not declare. Reading a mask the dataset never
        declared is a question about the dataset, and ignoring the pixel would hide it.
    """
    channels = image[..., :3].astype(np.uint32)
    keys = (channels[..., 0] << 16) | (channels[..., 1] << 8) | channels[..., 2]
    palette = np.array([_packed(entry["color"]) for entry in classes], dtype=np.uint32)
    order = np.argsort(palette)
    nearest = np.searchsorted(palette[order], keys)
    np.clip(nearest, 0, len(palette) - 1, out=nearest)  # a key past the end still has to compare
    if not np.array_equal(palette[order][nearest], keys):
        raise ValueError("the mask holds a colour that is not in the palette")
    labels = order[nearest]
    return {str(entry["name"]): labels == index for index, entry in enumerate(classes)}


def _packed(color: str) -> int:
    """Pack one ``#rrggbb`` colour into an integer, so a pixel can be compared with ``==``.

    Parameters
    ----------
    color : str
        The colour, as a description publishes it.

    Returns
    -------
    int
        Red in the top eight bits, blue in the bottom.

    Raises
    ------
    ValueError
        The colour is not six hexadecimal digits.
    """
    text = color.removeprefix("#")
    if len(text) != 6:
        raise ValueError(f"a colour must be #rrggbb, got {color!r}")
    red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    return (red << 16) | (green << 8) | blue


def _decode_objects(body: dict[str, Any]) -> None:
    """Attach each object's decoded ``mask``, in place.

    Parameters
    ----------
    body : dict
        A prediction or an image detail, either of which carries ``objects`` where it has geometry.
    """
    for obj in cast("list[dict[str, Any]] | None", body.get("objects")) or []:
        rle = obj.get("rle")
        if isinstance(rle, dict):
            obj["mask"] = rle_decode(cast("str", rle["counts"]), cast("Sequence[int]", rle["size"]))


def _decode_density(body: dict[str, Any]) -> None:
    """Attach a counting answer's field as a two-dimensional ``grid``, in place.

    Parameters
    ----------
    body : dict
        A prediction. A mask answer carries no ``density`` and is left alone.

    Raises
    ------
    ValueError
        When the body's ``size`` and ``values`` disagree, which a correct backend cannot send.

    Notes
    -----
    The wire carries ``size`` and a flat row-major ``values``; adding ``grid`` is the same move as
    adding ``mask`` to an object, and the raw fields stay so a caller can still add the cells up.
    """
    density = body.get("density")
    if not isinstance(density, dict):
        return
    values = density.get("values")
    size = density.get("size")
    if not isinstance(values, list) or not isinstance(size, list) or len(size) != 2:
        return
    rows, columns = int(size[0]), int(size[1])
    if len(values) != rows * columns:
        raise ValueError(f"density says {rows}×{columns} but carries {len(values)} values")
    density["grid"] = np.asarray(values, dtype=float).reshape(rows, columns)


def _declared_side(declared: Mapping[str, Any]) -> int | None:
    """Read the largest side a model declared it runs at.

    Parameters
    ----------
    declared : mapping
        A ``GET /models/{id}`` body.

    Returns
    -------
    int or None
        The ceiling of the model's ``imgsz`` parameter, or ``None`` where it declares no such
        parameter — a model that resizes to one fixed side publishes that side as ``resolution``
        instead, and has no knob to cap.
    """
    parameters = declared.get("parameters")
    if not isinstance(parameters, list):
        return None
    for parameter in parameters:
        if not isinstance(parameter, dict) or parameter.get("name") != "imgsz":
            continue
        # `imgsz` publishes the sides it was actually run at, as a list, rather than a range: the
        # ceiling is the largest of them, and a value above it is clamped by the model anyway.
        values = parameter.get("values")
        if isinstance(values, list) and values:
            return int(values[-1])
        maximum = parameter.get("maximum")
        if isinstance(maximum, (int, float)):
            return int(maximum)
    return None


def _as_side(value: object) -> int | None:
    """Read a caller's ``imgsz`` value as a side, where it is one.

    Parameters
    ----------
    value : any
        The value the caller passed for ``imgsz``.

    Returns
    -------
    int or None
        The side, or ``None`` where the value is not a number. A value the client cannot read is
        left for the model to refuse: guessing here would resize to a side nobody asked for.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _prepare_upload(image: str | Path | bytes, target_side: int) -> bytes:
    """Prepare one photograph for upload, at the side the model will resize it to.

    Parameters
    ----------
    image : str or pathlib.Path or bytes
        The photograph, as a path or already read.
    target_side : int
        The longest side the result may have, in pixels.

    Returns
    -------
    bytes
        A JPEG of at most ``target_side`` pixels on its longest side, upright, with no EXIF left.
        **The caller's own object** where the image already fits: an image at or under the target
        keeps its bytes, its quality and its metadata. Bytes Pillow cannot read are also returned
        untouched — the API is the authority on what it decodes, and refusing here would replace
        its ``400`` with this client's opinion.

    Notes
    -----
    ``Image.draft`` is what makes this cheap for JPEGs: it tells the decoder to hand back a
    one-quarter or one-eighth scale, so a 12 MP photo is never fully decoded. Pillow only applies it
    to JPEG, which is the format an upload almost always is; a PNG is decoded at full size because
    its decoder has no shortcut.

    The EXIF transpose comes before the resize because the orientation tag is about the photograph
    as a whole, and a rotation after a non-square resize would have to resize twice to fit again.
    """
    if target_side <= 0:
        raise ValueError(f"target_side must be positive, got {target_side}")
    payload = image if isinstance(image, bytes) else Path(image).read_bytes()
    try:
        opened = Image.open(io.BytesIO(payload))
    except (UnidentifiedImageError, OSError):
        return payload
    with opened:
        if max(opened.size) <= target_side:
            # The header says it already fits: no decode, no re-encode, the file goes as it is.
            return payload
        if opened.format == "JPEG":
            # Before the first pixel is decoded; a no-op for anything that is not a JPEG.
            opened.draft("RGB", (target_side, target_side))
        upright = ImageOps.exif_transpose(opened).convert("RGB")
        # Pillow's own rule: fit inside the square, keep the aspect ratio, never enlarge. Anything
        # already smaller is left alone, which is also why the header check above can return early.
        upright.thumbnail((target_side, target_side), Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        upright.save(encoded, format="JPEG", quality=UPLOAD_QUALITY)
    return encoded.getvalue()


def _upload(image: str | Path | bytes) -> tuple[str, bytes, str]:
    """Read an upload into the three things a multipart part needs.

    Parameters
    ----------
    image : str or pathlib.Path or bytes
        The photograph, as a path or already read.

    Returns
    -------
    tuple of (str, bytes, str)
        A file name, the bytes, and a media type. The API decides the real type from the bytes, so
        the name and type here are only what multipart requires.
    """
    if isinstance(image, bytes):
        return "upload.jpg", image, "application/octet-stream"
    path = Path(image)
    suffix = path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    return path.name, path.read_bytes(), media_type


def _segment(value: str) -> str:
    """Quote one path segment, so an id can never add a path of its own.

    Parameters
    ----------
    value : str
        A dataset or model id.

    Returns
    -------
    str
        The value, quoted.
    """
    return quote(str(value), safe="")


def _error(response: requests.Response) -> ApiError:
    """Turn a refusal into an :class:`ApiError`.

    Parameters
    ----------
    response : requests.Response
        The answer, whose status is at least 400.

    Returns
    -------
    ApiError
        The API's own code and message where the body carries them, and a code by status class
        where it does not.
    """
    try:
        detail = response.json().get("error", {})
    except ValueError:
        detail = {}
    failure = "internal_error" if response.status_code >= 500 else "invalid_request"
    code = detail.get("code") or failure
    message = detail.get("message") or response.text.strip() or response.reason
    return ApiError(response.status_code, str(code), str(message))
