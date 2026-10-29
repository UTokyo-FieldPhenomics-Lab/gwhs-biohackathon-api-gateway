"""Global Wheat API gateway application.

The surface is small and every part of it is deliberate. ``/health`` and the two pages this host
serves answer without a key because they carry no data — liveness, a contract that is public in
this repository anyway, and a page every number on which comes from a call the reader's own key
makes — and the two pages are also where a reader puts that key. Every other path, including paths
that do not exist, needs a participant token, because the people this serves are somewhere else on
the internet and nothing but that token stands between them and the host.

Key authentication, the error contract, and liveness live here. The dataset surface and the model
surface are both included from here, and both inherit the gate by adding a route rather than by
remembering to.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, nullcontext, suppress
from enum import StrEnum
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from biohack_gateway.admission import UploadQueue, UploadRefused, UploadSlots
from biohack_gateway.auth import Authenticator
from biohack_gateway.cache import CacheStats, ResultCache
from biohack_gateway.csp import new_nonce, policy, with_nonce
from biohack_gateway.dataset_index import DEFAULT_INDEX_DIR, load_datasets
from biohack_gateway.datasets import DEFAULT_DATA_BASE_URL, create_dataset_router
from biohack_gateway.frames import frame_reporter
from biohack_gateway.limits import (
    DEFAULT_MAX_CONCURRENT_PER_TOKEN,
    ConcurrencyGate,
    LaneRefused,
)
from biohack_gateway.models import (
    DEFAULT_MODELS_FILE,
    MachineSummary,
    ModelRegistry,
    Transport,
    create_model_router,
    http_transport,
    load_models,
    watch_pool,
)
from biohack_gateway.play import PLAY_PATH, play_page
from biohack_gateway.tokens import DEFAULT_TOKENS_FILE, TokenPoolError

logger = logging.getLogger(__name__)

# uvicorn configures loggers of its own and never the root one, so without this the lines this
# package writes — the pool it read, the team a request is attributed to — are discarded before they
# reach journalctl. That attribution is what replaces a counted quota here, so it has to be visible.
# `basicConfig` leaves an existing root configuration alone, so a deployment that passes
# `--log-config` still wins.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

APP_VERSION = installed_version("biohack-gateway")
"""The installed package's version, so ``/health`` reports what is actually running, not what is
in the checkout it was started from."""

API_KEY_HEADER = "X-API-Key"
"""Where a participant's token arrives."""

API_KEY_SCHEME = "ApiKeyAuth"
"""The name the contract gives that header.

Swagger UI carries a credential into a request only for a scheme the document declares, so without
this the reference lists every endpoint and can try none of them. The name is arbitrary, the header
is not, and both are read from here rather than written out again.
"""

CONCURRENCY_ENV = "BIOHACK_MAX_CONCURRENT_PER_TOKEN"
"""The environment variable that overrides the per-token concurrency cap.

Unset uses `DEFAULT_MAX_CONCURRENT_PER_TOKEN`, a fairness policy independent of the pool's
published container capacity. An override supports load testing from one key and needs a restart.
A value of `0` disables the cap; a non-number is logged and falls back to the policy default.
"""

BACKEND_TOKEN_ENV = "BIOHACK_BACKEND_TOKEN"
"""The environment variable carrying the token the gateway presents to backends.

An environment variable rather than a file, because it is the one setting here that is a secret: the
token pool can be a file this package owns and prints team ids from, but this one is never written
down by us at all. An unset value is a gateway that cannot reach any model, which the predict routes
report as unavailable rather than letting a backend answer `401` that reads like the participant's
fault.
"""

_PADLOCK_CLOSED = (
    "M15.8 8H14V5.6C14 2.703 12.665 1 10 1 7.334 1 6 2.703 6 5.6V8H4c-.553 0-1 .646-1 "
    "1.199V17c0 .549.428 1.139.951 1.307l1.197.387C5.672 18.861 6.55 19 7.1 19h5.8c.549 0 "
    "1.428-.139 1.951-.307l1.196-.387c.524-.167.953-.757.953-1.306V9.199C17 8.646 16.352 8 "
    "15.8 8zM12 8H8V5.199C8 3.754 8.797 3 10 3c1.203 0 2 .754 2 2.199V8z"
)
"""Swagger UI's closed padlock, as its own icon component draws it.

Copied verbatim so the reference page can show the two states the other way round: see
`SWAGGER_KEY_BAR`, which is the only place either glyph is used.
"""

_PADLOCK_OPEN = (
    "M15.8 8H14V5.6C14 2.703 12.665 1 10 1 7.334 1 6 2.703 6 5.6V6h2v-.801C8 3.754 8.797 3 10"
    " 3c1.203 0 2 .754 2 2.199V8H4c-.553 0-1 .646-1 1.199V17c0 .549.428 1.139.951 "
    "1.307l1.197.387C5.672 18.861 6.55 19 7.1 19h5.8c.549 0 1.428-.139 "
    "1.951-.307l1.196-.387c.524-.167.953-.757.953-1.306V9.199C17 8.646 16.352 8 15.8 8z"
)
"""Swagger UI's open padlock, from the same icon set — and from the same version, since the
shapes only mean anything as a pair.
"""

DOCS_PARENT_ORIGIN = "https://docs.global-wheat.com"
"""The one site allowed to frame the reference page, and the one window it reports its height to.

One origin rather than two settings: the page tells the browser who may frame it, and it addresses
the height it reports to the same site. A harness that frames a local app passes its own.
"""

# The page a participant types a key into must not run, or load, a file somebody else can change.
# These three are fetched by the browser, so the reference cannot be closed behind the gate and
# still work; they are named one by one rather than mounted as a directory, so no fourth file
# becomes reachable by being put in `assets/`.
SWAGGER_UI_BUNDLE_PATH = "/docs/swagger-ui-bundle.js"
"""Swagger UI's bundle, from the release pinned by the file in `assets/`."""

SWAGGER_UI_CSS_PATH = "/docs/swagger-ui.css"
"""Swagger UI's stylesheet, from the same release — the two are a pair."""

SWAGGER_UI_LICENSE_PATH = "/docs/swagger-ui-bundle.js.LICENSE.txt"
"""The notices the bundle's first line points at, so a served file carries its licence with it."""

OPEN_PATHS = frozenset(
    {
        "/health",
        "/docs",
        PLAY_PATH,
        "/openapi.json",
        SWAGGER_UI_BUNDLE_PATH,
        SWAGGER_UI_CSS_PATH,
        SWAGGER_UI_LICENSE_PATH,
    }
)
"""The paths that answer without a key.

Every entry earns the exception the same way: it says nothing about the data and exists for someone
who has no key yet, and a page cannot be opened in a browser at all if the page itself needs a
header — nor can the two files that page loads. Two of them are also where a reader puts a key —
the reference and the model page — and a field for a secret in a page that anything may embed is a
field that anything may overlay, which is why each names the one site allowed to frame it. Every
other path, including ones that do not exist, is behind the gate.
"""

LANE_SUFFIXES = ("/predict/upload",)
"""The one route below a model that the middleware charges one of the pool's lanes for.

A lane is GPU time, and it is the only scarce thing a participant can hold: a metadata read is an
in-memory lookup that answers in microseconds and queues behind nothing. The per-token ceiling
exists so one team's loop cannot take every lane — so it counts GPU work and nothing else.

`POST /models/{model}/predict` is deliberately absent. It takes its lane in the handler, after the
answer has been looked for in the cache: a stored answer occupies no GPU, so charging it a lane
would refuse a participant while no machine was busy. An upload always reaches a backend, so the
middleware can charge it before the bytes are read — which is where a refusal is cheapest.

Before either route was distinguished from the rest, the middleware counted every request, which
refused a page's own load (a dataset listing, a row's detail, the model directory) on a pool whose
`max_in_flight` was one.
"""


def occupies_lane(path: str) -> bool:
    """Report whether the middleware must take a lane before serving a path.

    Parameters
    ----------
    path : str
        The request's path, already stripped of a trailing slash.

    Returns
    -------
    bool
        `True` for ``POST /models/{model}/predict/upload``, which always reaches a backend; `False`
        for everything else, including ``…/predict`` — it looks the answer up first and takes its
        own lane in `models.predict` only when it has to ask a machine.

    Notes
    -----
    A path test rather than a marker on the route handlers, because the middleware is where the
    team is known and it is one place: the alternative is a dependency every future route has to
    remember, and the entry that forgets it would be uncounted rather than obviously wrong. The
    suffix names the model surface's own route, and
    `test_the_middleware_charges_only_the_upload_route` is what keeps them in step.
    """
    return path.startswith("/models/") and path.endswith(LANE_SUFFIXES)


_ASSETS = Path(__file__).parent / "assets"
"""The page material this application serves, one language to a file.

Markup, script and styling live here rather than as strings in the modules that inject them, so a
page is edited as a page and every Python module in this package is Python. Almost all of it is
read at import and inlined into the answer; the exception is the two Swagger UI files, which are
1.9 MB together and are fetched by the browser under their own names, so inlining them would put a
megabyte and a half on every reference load. Nothing here depends on the working directory, and
nothing is mounted as a directory: the three names below are the only ones that can be fetched.
`gateway.md` says so.
"""

SWAGGER_UI_BUNDLE = (_ASSETS / "swagger-ui-bundle.js").read_bytes()
"""Swagger UI 5.34.0's bundle, vendored so the key-entry page runs code this repository pinned.

It was fetched once from ``swagger-ui-dist@5.34.0`` on jsdelivr; the version is the one to quote if
it is ever replaced, and the file's first line points at the notices vendored beside it. Before
this, the page asked a floating ``@5`` for its script, which put every key typed into it at the
mercy of a third party — the one place on this host where that matters.
"""

SWAGGER_UI_CSS = (_ASSETS / "swagger-ui.css").read_bytes()
"""The same release's stylesheet. Serving the pair from one directory is what keeps them a pair."""

SWAGGER_UI_LICENSE = (_ASSETS / "swagger-ui-bundle.js.LICENSE.txt").read_bytes()
"""The licences of what the bundle carries, as the bundle's own header names them."""

SWAGGER_FAVICON = "data:,"
"""A blank icon, deliberately.

FastAPI's default points the page at ``fastapi.tiangolo.com`` — one more third-party fetch on the
page that holds a key, for a picture. A browser that has no icon to show shows its own.
"""


SWAGGER_DARK_STYLE = f"<style>{(_ASSETS / 'swagger_dark.css').read_text(encoding='utf-8')}</style>"
"""Swagger UI in the documentation site's dark palette, carried by the page itself.

Swagger UI ships no dark theme — the CDN serves one stylesheet and no dark variant — so this one
lives in `assets/` and is read at import. It applies only when the page has been told which
side it is on: by the `?scheme=` it was framed with, by the site's message when the reader switches,
or by the operating system's preference when the page is opened on its own.
"""

SWAGGER_TRIM_STYLE = f"<style>{(_ASSETS / 'swagger_trim.css').read_text(encoding='utf-8')}</style>"
"""The chrome this contract makes meaningless, removed in both palettes.

Separate from the palette because it is not one: the response table's always-empty Links column and
the response's single-option Media type chooser are equally useless to a reader in light mode, and a
rule that had to justify its missing `html[data-scheme="dark"]` prefix would be a worse neighbour
than a second file. What each trim removes, and what it deliberately leaves alone, is in the file.
"""

SWAGGER_KEY_BAR = (
    (_ASSETS / "swagger_key_bar.html")
    .read_text(encoding="utf-8")
    .replace("__HEADER__", API_KEY_HEADER)
    .replace("__OPEN__", _PADLOCK_OPEN)
    .replace("__CLOSED__", _PADLOCK_CLOSED)
)
"""The field a participant puts their key into, and the little styling it adds.

It is injected hidden — it belongs inside Swagger UI's own servers band, between the description
and the call list, and a field that appeared at the top of the page first would be a flash of the
wrong thing. The script moves it there once the page has rendered, and takes it away entirely if it
cannot: what this row shows, Swagger UI already styles.
"""

SWAGGER_KEY_BAR_SCRIPT = (
    f"<script>{(_ASSETS / 'swagger_key_bar.js').read_text(encoding='utf-8')}</script>".replace(
        "__SCHEME__", API_KEY_SCHEME
    )
)
"""What makes the field work, injected at the end of the body where Swagger UI exists.

It hands the key to Swagger's own session, so the request carries the header; remembering it is
the shared store's job rather than Swagger's, which is why this page asks the framework not to
persist anything — two remembered copies of a secret cannot be cleared together, and the model
page has to be able to clear it too. A field that cannot do both removes itself, and only a
field that did both hides the standard Authorize button — a page whose script did not run keeps
the door it always had.
"""

API_KEY_STORE_SCRIPT = f"<script>{(_ASSETS / 'api_key.js').read_text(encoding='utf-8')}</script>"
"""The one place a key is remembered, shared with the model page.

Both framed pages are served by this host, so both read and write the same entry: a key entered on
either is the key the other uses, and Forget on either clears it for both. The reference page is the
one that *arms* Swagger UI, which is what makes a call carry the header; the store only remembers.
"""


def _with_key_auth(schema: dict[str, Any]) -> dict[str, Any]:
    """Declare in the contract the key the gate already demands.

    Every operation but liveness requires a token, and the gate enforces that whatever the document
    says — so this changes nothing about who gets in. What it changes is whether the reference can
    be used: Swagger UI carries a credential into a request only for a scheme the document declares
    and only for an operation that asks for it, so an undeclared key means every Try it out answers
    ``401`` however carefully the reader pastes their token.

    The requirement is written per operation rather than left to the document root, because that is
    what Swagger UI reads. An operation that declares its own security keeps it: that is how
    liveness says it needs none.

    Parameters
    ----------
    schema : dict
        The document FastAPI generated, mutated in place.

    Returns
    -------
    dict
        The same document, with the scheme and the requirements added.
    """
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})[API_KEY_SCHEME] = {
        "type": "apiKey",
        "in": "header",
        "name": API_KEY_HEADER,
    }
    required: list[dict[str, list[str]]] = [{API_KEY_SCHEME: []}]
    schema["security"] = required
    for path in schema.get("paths", {}).values():
        for method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
            operation = path.get(method)
            if operation is not None and "security" not in operation:
                operation["security"] = required
    return schema


class ErrorCode(StrEnum):
    """Machine-readable error codes shared by every endpoint.

    The set is the frozen initial contract and grows additively: adding a member is
    non-breaking, removing or renaming one is not.
    """

    INVALID_REQUEST = "invalid_request"
    INVALID_KEY = "invalid_key"
    NOT_FOUND = "not_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UPSTREAM_ERROR = "upstream_error"
    UNAVAILABLE = "unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL_ERROR = "internal_error"


_CODES_BY_STATUS: dict[int, ErrorCode] = {
    401: ErrorCode.INVALID_KEY,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.INVALID_REQUEST,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    429: ErrorCode.QUOTA_EXCEEDED,
    502: ErrorCode.UPSTREAM_ERROR,
    503: ErrorCode.UNAVAILABLE,
    504: ErrorCode.UPSTREAM_TIMEOUT,
}
"""The codes the framework's own rejections map onto.

Only the ones we expect to raise ourselves or to inherit from the router are named; anything else
falls back by class of status, which is the part of the contract a caller can act on anyway.

The two upstream codes are here because the model surface raises them by hand, and without them a
backend that failed would be reported as a crash in the gateway — the opposite of what the retry
rule tells a caller to do about it.
"""


class ErrorDetail(BaseModel):
    """The error payload nested inside :class:`ErrorResponse`.

    Attributes
    ----------
    code : ErrorCode
        Stable machine-readable discriminant.
    message : str
        Human-readable explanation, safe to surface to the caller.
    """

    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """The one error body shape for the whole API.

    Attributes
    ----------
    error : ErrorDetail
        The error payload, nested so the top level can grow without touching it.
    """

    error: ErrorDetail


class HealthResponse(BaseModel):
    """What the gateway says about itself without a key.

    Attributes
    ----------
    status : Literal["ok"]
        Whether the process is serving. A gateway that cannot say so does not answer at all.
    version : str
        The version of the installed package that is answering.
    """

    status: Literal["ok"]
    version: str


class PoolLink(BaseModel):
    """The one link every machine shares, as upload admission sees it.

    The budget is one link, not one machine, so these figures are stated once rather than repeated
    on every row of `machines`: a number that is the same everywhere is not a fact about a machine.

    Attributes
    ----------
    bytes_in_flight : int
        Upload bytes the gateway has admitted and not yet released.
    bytes_per_s : float
        The link's measured capacity, in bytes per second — what the budget follows.
    budget_bytes : int
        The byte budget in force: the sum a burst may put on the link at once.
    waiting : int
        Uploads parked for room right now.
    """

    bytes_in_flight: int = 0
    bytes_per_s: float = 0.0
    budget_bytes: int = 0
    waiting: int = 0


class PoolResponse(BaseModel):
    """The operator's view of the pool — `GET /pool`, not part of the participant contract.

    Attributes
    ----------
    machines : list of MachineSummary
        One entry per machine, grouped exactly as the router counts them.
    link : PoolLink
        The shared link's figures, stated once.
    cache : CacheStats
        What the row cache holds and how often it answered. `bytes` is body bytes — the number the
        budget is counted in — not the size of the file those answers live in.
    """

    machines: list[MachineSummary] = Field(default_factory=list)
    link: PoolLink = Field(default_factory=PoolLink)
    cache: CacheStats = Field(default_factory=lambda: CacheStats(0, 0, 0, 0))


def _code_for(status_code: int) -> ErrorCode:
    """Choose the code for a rejection raised by the framework.

    Parameters
    ----------
    status_code : int
        The status the framework or a route asked for.

    Returns
    -------
    ErrorCode
        The named code when the status is one we plan for, otherwise ``internal_error`` for a
        server-side problem and ``invalid_request`` for a client-side one.
    """
    if code := _CODES_BY_STATUS.get(status_code):
        return code
    return ErrorCode.INTERNAL_ERROR if status_code >= 500 else ErrorCode.INVALID_REQUEST


def _error(
    status_code: int,
    code: ErrorCode,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the one error body.

    Every refusal in this application goes through here, which is what keeps the shape single.

    Parameters
    ----------
    status_code : int
        The HTTP status to answer with.
    code : ErrorCode
        The machine-readable code.
    message : str
        The explanation. It is shown to the caller, so it must never carry a token, a backend
        address, or anything else the caller should not learn.
    headers : mapping of str to str or None, optional
        Headers the refusal owes the caller — `Retry-After` on an admission refusal is the case,
        because a client told to come back later has to be told when.

    Returns
    -------
    JSONResponse
        The serialized :class:`ErrorResponse`.
    """
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=dict(headers) if headers else None,
    )


def create_app(
    tokens_path: Path = DEFAULT_TOKENS_FILE,
    index_dir: Path = DEFAULT_INDEX_DIR,
    data_base_url: str = DEFAULT_DATA_BASE_URL,
    docs_parent_origin: str = DOCS_PARENT_ORIGIN,
    models_path: Path = DEFAULT_MODELS_FILE,
    backend_token: str = "",
    model_transport: Transport = http_transport,
    max_concurrent_per_token: int | None = None,
    cache: ResultCache | None = None,
    admission: UploadQueue | None = None,
    upload_slots: UploadSlots | None = None,
) -> FastAPI:
    """Build the application.

    Every host setting is a parameter rather than a global so that a test can point an app at
    throwaway files, and so that nothing in this module depends on the host's layout. The dataset
    index is loaded here, once, which is what makes a republish arrive as a restart.

    Parameters
    ----------
    tokens_path : Path, optional
        The participant token pool to authenticate against.
    index_dir : Path, optional
        The directory of `<dataset>.db` files the dataset surface answers from. It need not exist.
    data_base_url : str, optional
        The public distribution the image addresses point at.
    docs_parent_origin : str, optional
        The one origin allowed to frame the reference page, which is also the window it reports its
        height to. A harness that frames a local app passes its own origin here.
    models_path : Path, optional
        The host's model-to-backend list. It need not exist: a gateway with no models configured
        answers an empty directory rather than refusing to start.
    backend_token : str, optional
        The token presented to backends as `X-Backend-Token`. Empty is a gateway that cannot reach
        any model, which predicts report as unavailable rather than as a client error.
    model_transport : Transport, optional
        How the gateway reaches a backend. Injectable so a test can answer for one without starting
        one, which is what lets the surface's tests exercise the real gate and error contract.
    max_concurrent_per_token : int or None, optional
        The cap on one team's uncached inferences in flight. `None` takes
        :data:`biohack_gateway.limits.DEFAULT_MAX_CONCURRENT_PER_TOKEN`, `0` disables the cap, and a
        number fixes it — the last two only for measuring the pool from one key. It is a policy
        number and is deliberately not derived from the pool's published width.
    cache : ResultCache or None, optional
        The store of row answers, built here when `None` — which reads `BIOHACK_CACHE`,
        `BIOHACK_CACHE_PATH` and `BIOHACK_CACHE_MAX_BYTES` and is what a host gets. A test passes
        its own instance, so nothing in the suite writes to the host's file, and the store is
        closed on shutdown.
    admission : UploadQueue or None, optional
        The byte budget uploads wait on. `None` builds one from the environment:
        `BIOHACK_UPLOAD_BYTES` caps the sum a burst may put on the link, and
        `BIOHACK_UPLOAD_WAIT_SECONDS` says how long a request may wait. That is what a host gets;
        a test passes its own, which is how a refusal is reached without moving a gigabyte.
    upload_slots : UploadSlots or None, optional
        How many uploads may be parsed or served at once. `None` builds one from
        `BIOHACK_UPLOAD_SLOTS`, or :data:`biohack_gateway.admission.DEFAULT_UPLOAD_SLOTS`. It is a
        separate parameter from `admission` because it bounds a different resource — the bodies
        that exist at once, which is this host's memory — and it is taken before the multipart body
        is parsed rather than after it has been measured.

    Returns
    -------
    FastAPI
        The gateway application, ready for uvicorn.
    """
    registry = ModelRegistry(load_models(models_path), backend_token, model_transport)
    slots = ConcurrencyGate(
        DEFAULT_MAX_CONCURRENT_PER_TOKEN
        if max_concurrent_per_token is None
        else max_concurrent_per_token
    )
    answers = ResultCache() if cache is None else cache
    uploads = UploadQueue() if admission is None else admission
    slots_for_uploads = UploadSlots() if upload_slots is None else upload_slots

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Keep the published pool count true for as long as the process serves.

        A task of its own rather than work inside a request: a request that arrives while a machine
        is off must not be the thing that discovers it, and a page that keeps asking must not make
        the gateway keep asking. It is cancelled on shutdown, so a reload never leaves two tasks
        asking the same machines — and the answer store is closed there too, so a restart's WAL is
        checkpointed rather than left for the next process to recover.
        """
        watching = asyncio.create_task(watch_pool(registry))
        try:
            yield
        finally:
            watching.cancel()
            with suppress(asyncio.CancelledError):
                await watching
            answers.close()

    app = FastAPI(
        title="Global Wheat API",
        version=APP_VERSION,
        description=(
            "Global Wheat dataset and model API for the BioHackathon. "
            "Guides and examples: https://docs.global-wheat.com"
        ),
        docs_url=None,  # registered below instead, so the page can carry the frame reporter
        # `/redoc` is the second reference UI FastAPI would serve, and it loads its bundle from a
        # floating CDN tag. It duplicates `/docs` and sits on the origin a key is stored under, so
        # it is one page no one needs and one way for a third party to reach a key.
        redoc_url=None,
        lifespan=lifespan,
    )

    # Declared before the dataset surface on purpose. The reference groups its calls in the order
    # the document lists its paths — Swagger UI's tag sorter keeps what it is given — so where this
    # route is registered decides what a reader meets first: the one call that needs no key, in a
    # group whose name says what it is rather than FastAPI's placeholder.
    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Liveness",
        description="Answers without a key, so a monitor or the deploy script can ask it.",
        tags=["liveness"],
        openapi_extra={"security": []},  # the one operation the contract says needs no key
    )
    async def health() -> HealthResponse:
        """Report that the process is serving.

        Returns
        -------
        HealthResponse
            The status and the version of the package that is answering.
        """
        return HealthResponse(status="ok", version=APP_VERSION)

    @app.get(
        "/pool",
        response_model=PoolResponse,
        response_model_exclude_unset=True,
        include_in_schema=False,
        summary="The pool as the router sees it",
    )
    def pool() -> PoolResponse:
        """Report the machines routing is choosing between, and what the cache is worth.

        Not part of the participant contract, and hidden from the reference for that reason: a
        machine's own name and the depth of its queue are the operator's business, while the model
        surface publishes only the reproducibility record of whoever answered. It is keyed like
        every other route — it is absent from :data:`OPEN_PATHS` — because a queue depth is still
        something about this host.

        Returns
        -------
        PoolResponse
            One entry per machine, grouped exactly as the router counts, each carrying the link's
            in-flight bytes and measured rate beside its own depth, its last failure kind and its
            age; and the cache's counts.

        Notes
        -----
        A plain `def` because it reads the answer store, whose SQLite query is blocking: FastAPI
        runs it in the thread pool, so a slow disk cannot hold the event loop every other route
        shares. Nothing here asks a backend — the probe owns that clock — so the numbers are the
        last verdict and its age, not a fresh measurement. `alive` and `last_failure` are read
        together: a machine that is answering and timed out a second ago is a path problem, and one
        that is not answering because it refused is a container that is not running.
        """
        held = uploads.stats()
        return PoolResponse(
            machines=registry.machines(),
            link=PoolLink(
                bytes_in_flight=held.bytes_in_flight,
                bytes_per_s=uploads.link.bytes_per_second,
                budget_bytes=held.budget_bytes,
                waiting=held.waiting,
            ),
            cache=answers.stats(),
        )

    authenticator = Authenticator(tokens_path)
    datasets = load_datasets(index_dir)
    app.include_router(create_dataset_router(datasets, data_base_url))
    app.include_router(
        create_model_router(
            registry,
            datasets,
            data_base_url,
            answers,
            slots,
            uploads,
        )
    )

    document = app.openapi

    def openapi_with_key_auth() -> dict[str, Any]:
        """Answer the contract with the key scheme the reference needs to be usable.

        Returns
        -------
        dict
            The document FastAPI generated, declared to require the header the gate reads.
        """
        if app.openapi_schema is None:
            app.openapi_schema = _with_key_auth(document())
        return app.openapi_schema

    app.openapi = openapi_with_key_auth  # type: ignore[method-assign]

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        """Answer the interactive reference, in the shape the documentation site's frame needs.

        Returns
        -------
        HTMLResponse
            FastAPI's own Swagger UI page with five additions: a field for the reader's key, the
            dark palette, the trims that remove chrome this contract makes meaningless, the script
            that reports this page's height to the site that frames it, and a policy that admits
            only this host's scripts and nothing else. The page is otherwise untouched, and a
            reader who opens `/docs` directly sees the same reference with the same field.
        """
        page = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — reference",
            # This host's own copy, and a blank icon: the page is where a key is typed, so it
            # loads nothing a third party can change and nothing it does not need.
            swagger_js_url=SWAGGER_UI_BUNDLE_PATH,
            swagger_css_url=SWAGGER_UI_CSS_PATH,
            swagger_favicon_url=SWAGGER_FAVICON,
            # Swagger is asked not to remember the key: remembering is one entry, shared with the
            # model page, and a second copy of a secret is one the other page cannot clear.
            swagger_ui_parameters={"persistAuthorization": False},
        )
        html = bytes(page.body).decode()
        html = html.replace('<div id="swagger-ui">', SWAGGER_KEY_BAR, 1)
        reporter = frame_reporter(docs_parent_origin, "#swagger-ui", extra="#key-bar")
        html = html.replace(
            "</body>",
            f"{SWAGGER_DARK_STYLE}{SWAGGER_TRIM_STYLE}"
            f"{API_KEY_STORE_SCRIPT}{SWAGGER_KEY_BAR_SCRIPT}{reporter}</body>",
        )
        nonce = new_nonce()
        return HTMLResponse(
            with_nonce(html, nonce),
            headers={
                "Content-Security-Policy": policy(nonce=nonce, frame_ancestors=docs_parent_origin)
            },
        )

    @app.get(SWAGGER_UI_BUNDLE_PATH, include_in_schema=False)
    async def swagger_ui_bundle() -> Response:
        """Serve Swagger UI's bundle to the reference page, without a key.

        The page cannot send a header for the files it loads, and the files are public source to
        begin with. An hour of caching keeps a reader's second visit from refetching 1.6 MB, while
        a redeploy is picked up by a reload rather than by a version prefix this host would have
        to keep true.
        """
        return _swagger_ui_asset(SWAGGER_UI_BUNDLE, "text/javascript")

    @app.get(SWAGGER_UI_CSS_PATH, include_in_schema=False)
    async def swagger_ui_stylesheet() -> Response:
        """Serve the stylesheet that belongs to the bundle, from the same release."""
        return _swagger_ui_asset(SWAGGER_UI_CSS, "text/css")

    @app.get(SWAGGER_UI_LICENSE_PATH, include_in_schema=False)
    async def swagger_ui_licenses() -> Response:
        """Serve the notices the bundle's own first line points at."""
        return _swagger_ui_asset(SWAGGER_UI_LICENSE, "text/plain")

    @app.get(PLAY_PATH, include_in_schema=False)
    async def play() -> HTMLResponse:
        """Answer the page a participant runs one model from, in the same frame as the reference.

        The two open pages earn the exception the same way, and this one earns it twice over: it
        carries no data of its own — every number on it comes from a call the reader's key makes —
        and it is the one place a reader types that key into a page this host serves, which is why
        only the documentation site may frame it. Serving it from here is also what makes the live
        call possible at all: the page is same-origin with the API, so no CORS is involved anywhere.

        Returns
        -------
        HTMLResponse
            The page, from :mod:`biohack_gateway.play`, carrying that page's own CSP.
        """
        return play_page(docs_parent_origin, data_base_url)

    @app.middleware("http")
    async def require_key(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Let the open paths through and demand a token everywhere else.

        Fail closed on purpose: the gate is applied to the request, not to a list of routes, so an
        endpoint added later is protected by existing. The team the token belongs to is recorded on
        the request and logged once, which is the attribution that stands in for a counted quota.

        **Only an upload is charged here, and it is charged twice.** A global slot is taken before
        the multipart body is parsed, because the parsed body is memory this host has a fixed amount
        of; a request that finds every slot taken is refused at once with a `Retry-After` rather
        than queued, so it never parks a worker. The per-team lane is what the gateway charges for
        GPU work, and it is taken only on paths :func:`occupies_lane` names.

        Parameters
        ----------
        request : Request
            The incoming request.
        call_next : RequestResponseEndpoint
            The rest of the stack.

        Returns
        -------
        Response
            The application's answer, or the refusal this middleware built.
        """
        path = request.url.path.rstrip("/") or "/"
        if path in OPEN_PATHS:
            return await call_next(request)

        try:
            team_id = authenticator.team_for(request.headers.get(API_KEY_HEADER, ""))
        except TokenPoolError:
            # Nobody can be authenticated until the file parses, and the caller is told only
            # that the gateway cannot serve them. The pool names its own problem where it is
            # read — once per change, rather than once per request as it would be here.
            return _error(
                503,
                ErrorCode.UNAVAILABLE,
                "the gateway cannot authenticate anyone right now",
            )

        if team_id is None:
            # The presented value is not echoed: it may be a typo of a real token.
            return _error(
                401,
                ErrorCode.INVALID_KEY,
                f"send a participant token in the {API_KEY_HEADER} header",
            )

        request.state.team_id = team_id
        # Two admissions are charged here, in order. The first counts receive slots, a resource: an
        # upload's multipart body is spooled — up to fifteen megabytes each — before its handler
        # runs, so the only place to bound how many bodies exist at once is before the parse, where
        # a refusal costs nothing. It refuses at once rather than waiting: a parked request would
        # hold the worker thread the pool exists to spare. The second is the fairness lane: a lane
        # is a GPU lane, so only work that reaches a backend takes one. An upload always does. A row
        # predict does not: it looks the answer up first and takes its own lane in `models.predict`
        # only on a miss, so a cached answer is never refused as "too many inferences" while no
        # machine is busy.
        uploading = occupies_lane(path)
        if uploading:
            try:
                slots_for_uploads.acquire()
            except UploadRefused as refused:
                logger.info(
                    "%s %s team=%s refused: all %d upload slots busy",
                    request.method,
                    request.url.path,
                    team_id,
                    slots_for_uploads.capacity,
                )
                return _error(
                    429,
                    ErrorCode.QUOTA_EXCEEDED,
                    str(refused),
                    headers={"Retry-After": str(refused.retry_after_seconds)},
                )
        held = (
            slots.hold(team_id, f"{request.method} {request.url.path}")
            if uploading
            else nullcontext()
        )
        try:
            with held:
                logger.info("%s %s team=%s", request.method, request.url.path, team_id)
                return await call_next(request)
        except LaneRefused as refused:
            # A response rather than a raised exception here: the application's handlers sit
            # *inside* this middleware, so nothing would turn an exception raised at this depth
            # into the contract's 429.
            return _error(429, ErrorCode.QUOTA_EXCEEDED, str(refused))
        finally:
            # The slot covers the parse and the answer, and it is given back on every path — a
            # lane, a `4xx` the route built, or a crash — because a slot that only rose would
            # refuse every upload for the life of the process.
            if uploading:
                slots_for_uploads.release()

    @app.exception_handler(LaneRefused)
    async def lane_refused(request: Request, exc: LaneRefused) -> JSONResponse:
        """Give a route's lane refusal the same 429 the middleware builds by hand.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : LaneRefused
            The refusal, carrying the sentence the limit wrote.

        Returns
        -------
        JSONResponse
            The contract's ``429``, with the same body the middleware returns for an upload.
        """
        return _error(429, ErrorCode.QUOTA_EXCEEDED, str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Answer invalid input with ``400`` instead of FastAPI's default ``422``.

        The contract has exactly one "bad parameter" response: ``400`` with an
        ``ErrorResponse`` body. Overriding the handler here keeps the generated API
        reference honest, since FastAPI would otherwise advertise ``422`` on every
        endpoint that accepts parameters.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : RequestValidationError
            The validation failure raised by FastAPI.

        Returns
        -------
        JSONResponse
            A ``400`` response carrying :class:`ErrorResponse`.
        """
        errors = exc.errors()
        message = errors[0]["msg"] if errors else "invalid request"
        return _error(400, ErrorCode.INVALID_REQUEST, message)

    @app.exception_handler(StarletteHTTPException)
    async def rejection(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Give the framework's own rejections — 404, 405, and whatever a route raises — one shape.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : StarletteHTTPException
            The rejection, carrying the status the framework or a route chose.

        Returns
        -------
        JSONResponse
            The status that was asked for, with the body this contract defines. Headers a route
            attached to the exception are carried too — `Retry-After` on an admission refusal is the
            case — because a rejection that arrives without them tells a client to retry with no
            idea when.
        """
        response = _error(exc.status_code, _code_for(exc.status_code), str(exc.detail))
        for name, value in (exc.headers or {}).items():
            response.headers[name] = value
        return response

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Answer a crash with the contract rather than with a traceback.

        The exception is not logged here: the server re-raises it after this handler answers, so its
        own log already carries the traceback, and printing it twice helps nobody.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : Exception
            The failure. Its text is deliberately not passed on.

        Returns
        -------
        JSONResponse
            A ``500`` response carrying :class:`ErrorResponse`.
        """
        return _error(500, ErrorCode.INTERNAL_ERROR, "unexpected server error")

    return app


def _swagger_ui_asset(body: bytes, media_type: str) -> Response:
    """Answer one of the reference page's own files.

    Parameters
    ----------
    body : bytes
        The file, read from ``assets/`` when this module was imported.
    media_type : str
        Its content type, which is what a browser decides from before it runs or applies it.

    Returns
    -------
    Response
        The file, cacheable for an hour. Long enough that a reader's second visit to the reference
        refetches nothing, short enough that a redeploy is picked up without a version prefix this
        host would have to keep true.
    """
    return Response(
        body,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _env_concurrency() -> int | None:
    """Read the concurrency override, treating anything unusable as "not set".

    Returns
    -------
    int or None
        The override, or `None` when the variable is unset, empty, or not a number — a host with a
        typo in its environment should serve under the default policy rather than refuse every
        request after the first few.
    """
    raw = os.environ.get(CONCURRENCY_ENV, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using the default cap", CONCURRENCY_ENV, raw)
        return None


app = create_app(
    backend_token=os.environ.get(BACKEND_TOKEN_ENV, ""),
    max_concurrent_per_token=_env_concurrency(),
)
"""The object uvicorn serves: ``uvicorn biohack_gateway.app:app``.

The host supplies the backend token and optional per-team cap override through its environment.
The cache and upload admission constructors read their own documented environment settings.
Dataset and token paths remain factory parameters with defaults; tests pass isolated state.
"""
