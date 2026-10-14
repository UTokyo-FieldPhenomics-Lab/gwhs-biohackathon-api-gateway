"""Global Wheat API gateway application.

The surface is small and every part of it is deliberate. ``/health`` and the generated documentation
pages answer without a key because they carry no data — liveness, and a contract that is public in
this repository anyway. Everything else, including paths that do not exist, needs a participant
token, because the people this serves are somewhere else on the internet and nothing but that token
stands between them and the host.

Key authentication, the error contract, and liveness live here. The dataset and model surfaces
arrive on top of them, and inherit the gate by adding a route rather than by remembering to.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from biohack_gateway.auth import Authenticator
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

OPEN_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})
"""The paths that answer without a key.

Both entries earn the exception the same way: they say nothing about the data and exist for someone
who has no key yet, and the documentation pages cannot be reached from a browser at all if the page
itself needs a header. Every other path — including ones that do not exist — is behind the gate.
"""


class ErrorCode(StrEnum):
    """Machine-readable error codes shared by every endpoint.

    The set is the frozen initial contract and grows additively: adding a member is
    non-breaking, removing or renaming one is not.
    """

    INVALID_REQUEST = "invalid_request"
    INVALID_KEY = "invalid_key"
    NOT_FOUND = "not_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    UPSTREAM_ERROR = "upstream_error"
    UNAVAILABLE = "unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL_ERROR = "internal_error"


_CODES_BY_STATUS: dict[int, ErrorCode] = {
    401: ErrorCode.INVALID_KEY,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.INVALID_REQUEST,
    429: ErrorCode.QUOTA_EXCEEDED,
    503: ErrorCode.UNAVAILABLE,
}
"""The codes the framework's own rejections map onto.

Only the ones we expect to raise ourselves or to inherit from the router are named; anything else
falls back by class of status, which is the part of the contract a caller can act on anyway.
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


def create_app(tokens_path: Path = DEFAULT_TOKENS_FILE) -> FastAPI:
    """Build the application.

    The pool is a parameter rather than a global so that a test can point an app at a throwaway
    file, and so that nothing in this module depends on the host's layout.

    Parameters
    ----------
    tokens_path : Path, optional
        The participant token pool to authenticate against.

    Returns
    -------
    FastAPI
        The gateway application, ready for uvicorn.
    """
    app = FastAPI(
        title="Global Wheat API",
        version=APP_VERSION,
        description="Global Wheat dataset and model API for the BioHackathon.",
    )
    authenticator = Authenticator(tokens_path)

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
        logger.info("%s %s team=%s", request.method, request.url.path, team_id)
        return await call_next(request)

    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Liveness",
        description="Answers without a key, so a monitor or the deploy script can ask it.",
    )
    async def health() -> HealthResponse:
        """Report that the process is serving.

        Returns
        -------
        HealthResponse
            The status and the version of the package that is answering.
        """
        return HealthResponse(status="ok", version=APP_VERSION)

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


app = create_app()
"""The object uvicorn serves: ``uvicorn biohack_gateway.app:app``."""
