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

Dependencies are ``requests`` and ``numpy``, and nothing else.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import numpy as np
import requests

__all__ = [
    "API_KEY_HEADER",
    "BASE_ENV",
    "DEFAULT_BASE",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_TIMEOUT",
    "KEY_ENV",
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
            ``{"model", "objects", "resolution_used", "computed_by"}``, with ``mask`` on every
            object when ``decode`` is true.

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
        return body

    def predict_file(
        self,
        model: str,
        image: str | Path | bytes,
        *,
        decode: bool = True,
        **params: object,
    ) -> dict[str, Any]:
        """Run one model on your own photograph.

        Parameters
        ----------
        model : str
            The model id.
        image : str or pathlib.Path or bytes
            The file to send, as a path or already in memory. JPEG, PNG, WebP, TIFF or GIF, at most
            15 MB and 50 megapixels; the API decides by the bytes rather than by a declared type.
        decode : bool, optional
            Attach each object's ``mask``, decoded.
        **params
            The model's own control parameters, encoded as in :meth:`predict` — a geometry value is
            one JSON array in one parameter.

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
        body: dict[str, Any] = self._call(
            "POST",
            f"/models/{_segment(model)}/predict/upload",
            params=_model_params(params),
            files={"file": (name, payload, media_type)},
        )
        if decode:
            _decode_objects(body)
        return body

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
