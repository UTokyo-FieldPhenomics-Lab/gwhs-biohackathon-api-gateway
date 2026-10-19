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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from enum import StrEnum
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from biohack_gateway.auth import Authenticator
from biohack_gateway.dataset_index import DEFAULT_INDEX_DIR, load_datasets
from biohack_gateway.datasets import DEFAULT_DATA_BASE_URL, create_dataset_router
from biohack_gateway.frames import frame_reporter
from biohack_gateway.limits import DEFAULT_MAX_CONCURRENT_PER_TOKEN, ConcurrencyGate
from biohack_gateway.models import (
    DEFAULT_MODELS_FILE,
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

Unset is the normal state: the cap is then the pool's own width, read from the declarations
`GET /models` sums. It exists for one purpose — a load test from a single key, which the pool's
width would refuse after three inferences — so raising it is a deliberate act on the host and needs
a restart to take effect. A value of `0` disables the cap, and a value that is not a number is
ignored with a log line rather than silently becoming zero.
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

OPEN_PATHS = frozenset({"/health", "/docs", PLAY_PATH, "/redoc", "/openapi.json"})
"""The paths that answer without a key.

Every entry earns the exception the same way: it says nothing about the data and exists for someone
who has no key yet, and a page cannot be opened in a browser at all if the page itself needs a
header. Two of them are also where a reader puts a key — the reference and the model page — and a
field for a secret in a page that anything may embed is a field that anything may overlay, which is
why each names the one site allowed to frame it. Every other path, including ones that do not exist,
is behind the gate.
"""

LANE_SUFFIXES = ("/predict", "/predict/upload")
"""The two routes below a model that occupy one of the pool's lanes.

A lane is GPU time, and it is the only scarce thing a participant can hold: a metadata read is an
in-memory lookup that answers in microseconds and queues behind nothing. The per-token ceiling
exists so one team's loop cannot take every lane — so it counts these two routes and nothing else.
Before this was made explicit the middleware counted every request, which refused a page's own load
(a dataset listing, a row's detail, the model directory) on a pool whose `max_in_flight` was one.
"""


def occupies_lane(path: str) -> bool:
    """Report whether a route consumes one of the pool's inference lanes.

    Parameters
    ----------
    path : str
        The request's path, already stripped of a trailing slash.

    Returns
    -------
    bool
        `True` for ``POST /models/{model}/predict`` and ``…/predict/upload``, which are the only two
        routes that reach a backend's GPU; `False` for everything else, including the model
        directory and the whole dataset surface.

    Notes
    -----
    A path test rather than a marker on the route handlers, because the middleware is where the
    team is known and it is one place: the alternative is a dependency every future route has to
    remember, and the entry that forgets it would be uncounted rather than obviously wrong. The
    suffix names the model surface's own two operations, and
    `test_the_lane_is_only_the_two_predict_routes` is what keeps them in step.
    """
    return path.startswith("/models/") and path.endswith(LANE_SUFFIXES)


_ASSETS = Path(__file__).parent / "assets"
"""The page material this application serves, one language to a file.

Markup, script and styling live here rather than as strings in the modules that inject them, so a
page is edited as a page and every Python module in this package is Python. They are read at
import and inlined into the answer, never mounted: what this host serves at ``/docs`` is a
document the application carries, not a directory whose contents anything may fetch by
guessing a name, and nothing here depends on the working directory. `gateway.md` says so.
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


def _error(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
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

    Returns
    -------
    JSONResponse
        The serialized :class:`ErrorResponse`.
    """
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_app(
    tokens_path: Path = DEFAULT_TOKENS_FILE,
    index_dir: Path = DEFAULT_INDEX_DIR,
    data_base_url: str = DEFAULT_DATA_BASE_URL,
    docs_parent_origin: str = DOCS_PARENT_ORIGIN,
    models_path: Path = DEFAULT_MODELS_FILE,
    backend_token: str = "",
    model_transport: Transport = http_transport,
    max_concurrent_per_token: int | None = None,
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
        The cap on one token's requests in flight. `None` takes
        :data:`biohack_gateway.limits.DEFAULT_MAX_CONCURRENT_PER_TOKEN`, `0` disables the cap, and a
        number fixes it — the last two only for measuring the pool from one key. It is a policy
        number and is deliberately not derived from the pool's published width.

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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Keep the published pool count true for as long as the process serves.

        A task of its own rather than work inside a request: a request that arrives while a machine
        is off must not be the thing that discovers it, and a page that keeps asking must not make
        the gateway keep asking. It is cancelled on shutdown, so a reload never leaves two tasks
        asking the same machines.
        """
        watching = asyncio.create_task(watch_pool(registry))
        try:
            yield
        finally:
            watching.cancel()
            with suppress(asyncio.CancelledError):
                await watching

    app = FastAPI(
        title="Global Wheat API",
        version=APP_VERSION,
        description=(
            "Global Wheat dataset and model API for the BioHackathon. "
            "Guides and examples: https://docs.global-wheat.com"
        ),
        docs_url=None,  # registered below instead, so the page can carry the frame reporter
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

    authenticator = Authenticator(tokens_path)
    datasets = load_datasets(index_dir)
    app.include_router(create_dataset_router(datasets, data_base_url))
    app.include_router(
        create_model_router(
            registry,
            datasets,
            data_base_url,
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
            FastAPI's own Swagger UI page with four additions: a field for the reader's key, the
            dark palette, the trims that remove chrome this contract makes meaningless, and the
            script that reports this page's height to the site that frames it. The page is otherwise
            untouched, and a reader who opens `/docs` directly sees the same reference with the same
            field.
        """
        page = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — reference",
            # Swagger is asked not to remember the key: remembering is one entry, shared with the
            # model page, and a second copy of a secret is one the other page cannot clear.
            swagger_ui_parameters={"persistAuthorization": False},
        )
        html = bytes(page.body).decode()
        html = html.replace('<div id="swagger-ui">', SWAGGER_KEY_BAR, 1)
        reporter = frame_reporter(docs_parent_origin, "#swagger-ui", extra="#key-bar")
        return HTMLResponse(
            html.replace(
                "</body>",
                f"{SWAGGER_DARK_STYLE}{SWAGGER_TRIM_STYLE}"
                f"{API_KEY_STORE_SCRIPT}{SWAGGER_KEY_BAR_SCRIPT}{reporter}</body>",
            ),
            headers={"Content-Security-Policy": f"frame-ancestors {docs_parent_origin}"},
        )

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
        return play_page(docs_parent_origin)

    @app.middleware("http")
    async def require_key(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Let the open paths through and demand a token everywhere else.

        Fail closed on purpose: the gate is applied to the request, not to a list of routes, so an
        endpoint added later is protected by existing. The team the token belongs to is recorded on
        the request and logged once, which is the attribution that stands in for a counted quota.

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
        # A slot is a GPU lane, so only the routes that reach a backend take one. Counting a
        # metadata read would refuse a participant's page load on the strength of work no machine
        # is doing, which is the opposite of what the cap is for.
        counted = occupies_lane(path)
        if counted and not slots.acquire(team_id):
            # Refused here rather than queued: the cap is the pool's own width, so a team at it is
            # asking for more lanes than exist, and a wait nobody can see is worse than an answer
            # the client can act on.
            logger.info(
                "%s %s team=%s refused: %d lanes busy",
                request.method,
                request.url.path,
                team_id,
                slots.in_flight(team_id),
            )
            return _error(
                429,
                ErrorCode.QUOTA_EXCEEDED,
                f"at most {slots.ceiling()} inferences at once per token; retry when one finishes",
            )
        logger.info("%s %s team=%s", request.method, request.url.path, team_id)
        try:
            return await call_next(request)
        finally:
            if counted:
                slots.release(team_id)

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
            The status that was asked for, with the body this contract defines.
        """
        return _error(exc.status_code, _code_for(exc.status_code), str(exc.detail))

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


def _env_concurrency() -> int | None:
    """Read the concurrency override, treating anything unusable as "not set".

    Returns
    -------
    int or None
        The override, or `None` when the variable is unset, empty, or not a number — a host with a
        typo in its environment should serve under the pool's own width rather than refuse every
        request after the first few.
    """
    raw = os.environ.get(CONCURRENCY_ENV, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using the pool's width", CONCURRENCY_ENV, raw)
        return None


app = create_app(
    backend_token=os.environ.get(BACKEND_TOKEN_ENV, ""),
    max_concurrent_per_token=_env_concurrency(),
)
"""The object uvicorn serves: ``uvicorn biohack_gateway.app:app``.

The one setting read from the environment rather than passed in: the backend token is a secret, so
it cannot live in a file this repository ships, and systemd supplies it from the host's environment.
Nothing else about the host is read here — the paths have defaults and the tests pass their own.
"""
