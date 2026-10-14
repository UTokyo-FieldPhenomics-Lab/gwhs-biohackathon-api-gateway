"""Contract tests for the single error body shape."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from biohack_gateway.app import ErrorCode, ErrorResponse


def test_error_code_set_is_the_frozen_initial_set() -> None:
    """The code set matches the frozen contract exactly, growth included.

    Editing this expectation is how a code is added: the set grows additively, so a member that is
    removed or renamed has to break this test, and a new one has to be recorded here on purpose.
    ``internal_error`` is the first of those, added when the gateway began answering unhandled
    failures with the contract instead of with a traceback.
    """
    assert {code.value for code in ErrorCode} == {
        "invalid_request",
        "invalid_key",
        "not_found",
        "quota_exceeded",
        "upstream_error",
        "unavailable",
        "upstream_timeout",
        "internal_error",
    }


def test_error_response_serialises_to_the_frozen_shape() -> None:
    """The body is exactly ``{"error": {"code", "message"}}``."""
    payload = ErrorResponse.model_validate(
        {"error": {"code": "not_found", "message": "no such image"}}
    )
    assert payload.model_dump(mode="json") == {
        "error": {"code": "not_found", "message": "no such image"}
    }


def test_unknown_error_code_is_rejected() -> None:
    """A code outside the enum is not part of the contract."""
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"error": {"code": "teapot", "message": "x"}})
