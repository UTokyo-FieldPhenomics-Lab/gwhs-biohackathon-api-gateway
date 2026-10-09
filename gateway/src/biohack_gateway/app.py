"""Global Wheat API gateway application.

Placeholder skeleton. The real gateway responsibilities (key authentication, per-key and
global concurrency gates, backend routing with failover, and cost logging) are implemented
next. This file stays minimal so that ``uvicorn biohack_gateway.app:app`` starts and the API
reference can be generated from a real app object.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


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


app = FastAPI(
    title="Global Wheat API",
    version="0.1.0",
    description="Global Wheat dataset and model API for the BioHackathon.",
)


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
    payload = ErrorResponse(error=ErrorDetail(code=ErrorCode.INVALID_REQUEST, message=message))
    return JSONResponse(status_code=400, content=payload.model_dump(mode="json"))
