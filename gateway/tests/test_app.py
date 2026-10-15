"""The live HTTP surface: health, the error contract, and key authentication.

These tests pin what a participant — or the deploy script's smoke test, or a stranger who found the
host — sees. The three are one subject on purpose: the same middleware decides who gets in, and the
same helper builds every answer that says no.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from biohack_gateway.app import (
    API_KEY_HEADER,
    API_KEY_SCHEME,
    DOCS_PARENT_ORIGIN,
    create_app,
)
from biohack_gateway.tokens import TokenPool

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"
KEY = {"X-API-Key": TOKEN}


def _pool(path: Path) -> Path:
    """Write a pool holding one team's token, and return the path.

    Parameters
    ----------
    path : Path
        Where to write the pool.

    Returns
    -------
    Path
        The same path, so a caller can hand it to the app.
    """
    TokenPool({TEAM: TOKEN}).save(path)
    return path


@pytest.fixture
def pool_path(tmp_path: Path) -> Path:
    """Write a pool holding one team's token, and give its path."""
    return _pool(tmp_path / "tokens.json")


@pytest.fixture
def client(pool_path: Path, tmp_path: Path) -> Iterator[TestClient]:
    """Give a client for an app reading that pool, which answers errors instead of raising them.

    The index directory is a throwaway that does not exist: these tests are about the gate, and
    reading the host's real index would couple them to whatever has been published on this machine.
    """
    with TestClient(
        create_app(tokens_path=pool_path, index_dir=tmp_path / "index"),
        raise_server_exceptions=False,
    ) as test_client:
        yield test_client


@pytest.fixture
def routes(client: TestClient) -> Iterator[FastAPI]:
    """Give the app behind :func:`client`, with any route a test adds removed afterwards."""
    app = cast(FastAPI, client.app)
    before = len(app.router.routes)
    yield app
    del app.router.routes[before:]


def test_health_answers_without_a_key(client: TestClient) -> None:
    """Liveness is nobody's secret: a monitor and the deploy smoke test may both ask it."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": installed_version("biohack-gateway")}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_the_contract_pages_answer_without_a_key(client: TestClient, path: str) -> None:
    """The page that lists the endpoints cannot send itself a header, and the contract is public."""
    assert client.get(path).status_code == 200


def test_anything_else_without_a_key_is_refused(client: TestClient) -> None:
    """Fail closed, including paths that do not exist, so the open surface cannot be enumerated."""
    response = client.get("/")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_key"


def test_a_key_nobody_issued_is_refused(client: TestClient) -> None:
    """An unknown key is refused, and is never echoed back into the answer."""
    presented = "a-guess-at-a-token"
    response = client.get("/", headers={"X-API-Key": presented})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_key"
    assert presented not in response.text


def test_a_valid_key_reaches_the_router(client: TestClient) -> None:
    """The gate opens for a key the pool holds, and what happens next is the router's business."""
    response = client.get("/", headers=KEY)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_method_that_does_not_exist_keeps_the_shape(client: TestClient) -> None:
    """``405`` is a rejection like any other, so it carries the one error body."""
    response = client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "invalid_request"


def test_invalid_input_is_400_not_422(client: TestClient, routes: FastAPI) -> None:
    """The contract has one bad-parameter answer, and FastAPI's default 422 is not part of it."""

    @routes.get("/probe")
    def probe(limit: int) -> dict[str, int]:
        return {"limit": limit}

    response = client.get("/probe", params={"limit": "many"}, headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_route_added_later_is_protected_by_default(client: TestClient, routes: FastAPI) -> None:
    """The gate is the default an endpoint inherits, not a decorator somebody has to remember."""

    @routes.get("/later")
    def later() -> dict[str, bool]:
        return {"reached": True}

    assert client.get("/later").status_code == 401
    assert client.get("/later", headers=KEY).status_code == 200


def test_an_unhandled_error_keeps_the_shape(client: TestClient, routes: FastAPI) -> None:
    """A crash is still the contract, and it says nothing about what broke or what it contained."""

    @routes.get("/boom")
    def boom() -> None:
        raise RuntimeError("a secret that must not reach the caller")

    response = client.get("/boom", headers=KEY)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text


def test_a_missing_pool_closes_the_gate(tmp_path: Path) -> None:
    """Before the first token is issued nobody gets in, which is the safe direction to fail in."""
    with TestClient(
        create_app(tokens_path=tmp_path / "absent.json", index_dir=tmp_path / "index"),
        raise_server_exceptions=False,
    ) as client:
        assert client.get("/", headers=KEY).status_code == 401
        assert client.get("/health").status_code == 200


def test_an_unreadable_pool_is_unavailable_and_heals(tmp_path: Path) -> None:
    """A pool that does not parse is the operator's problem: say so, keep serving, and recover."""
    path = tmp_path / "tokens.json"
    path.write_text("{]. not json", encoding="utf-8")

    with TestClient(
        create_app(tokens_path=path, index_dir=tmp_path / "index"), raise_server_exceptions=False
    ) as client:
        response = client.get("/", headers=KEY)

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
        assert client.get("/health").status_code == 200

        _pool(path)
        assert client.get("/", headers=KEY).status_code == 404


def test_an_authenticated_request_is_attributed_to_its_team(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The team id in the log is what replaces a counted quota, and the token is never in it."""
    with caplog.at_level(logging.INFO, logger="biohack_gateway.app"):
        client.get("/", headers=KEY)

    assert f"team={TEAM}" in caplog.text
    assert TOKEN not in caplog.text


def test_the_contract_declares_the_key_the_gate_demands(client: TestClient) -> None:
    """The reference cannot send a header it was never told about: the scheme is what tells it."""
    scheme = client.get("/openapi.json").json()["components"]["securitySchemes"][API_KEY_SCHEME]

    assert scheme == {"type": "apiKey", "in": "header", "name": API_KEY_HEADER}


def test_every_operation_but_liveness_is_declared_to_need_the_key(client: TestClient) -> None:
    """Declare the key operation by operation.

    Swagger UI attaches a credential per operation, so the requirement is written on each one rather
    than left to be inherited — an operation without it is tried without a key.
    """
    operations = {
        (method, path): operation.get("security")
        for path, item in client.get("/openapi.json").json()["paths"].items()
        for method, operation in item.items()
    }

    assert operations, "the document has no operations to check"
    assert {
        where: needed for where, needed in operations.items() if needed != [{API_KEY_SCHEME: []}]
    } == {("get", "/health"): []}


def test_the_document_root_declares_the_requirement_too(client: TestClient) -> None:
    """Someone reading the raw document should not have to infer the key from the description."""
    assert client.get("/openapi.json").json()["security"] == [{API_KEY_SCHEME: []}]


def test_the_reference_page_carries_a_key_field(client: TestClient) -> None:
    """The reference page carries its own field for the key.

    The frame cannot show Swagger UI's own dialog where the reader is looking — the dialog centres
    on a frame that is as tall as the page — so a field at the top is how a key gets in.
    """
    page = client.get("/docs").text

    assert 'id="key-bar"' in page
    assert f'"{API_KEY_SCHEME}"' in page
    assert "preauthorizeApiKey" in page
    assert page.count('<div id="swagger-ui">') == 1  # the field is inserted before it, not over it


def test_the_reference_page_remembers_the_key_it_was_given(client: TestClient) -> None:
    """Ask Swagger UI to remember the key.

    A key is good for the whole event, and re-pasting it on every visit is friction a participant
    does not need.
    """
    assert '"persistAuthorization": true' in client.get("/docs").text


def test_the_reference_page_may_only_be_framed_by_the_site(client: TestClient) -> None:
    """A field for a secret belongs in a page that only one site may frame."""
    response = client.get("/docs")

    assert response.headers["content-security-policy"] == f"frame-ancestors {DOCS_PARENT_ORIGIN}"
    assert f'"{DOCS_PARENT_ORIGIN}"' in response.text


def test_the_parent_origin_is_a_setting(tmp_path: Path) -> None:
    """The header and the reporter name one origin, so a harness can frame what it develops."""
    parent = "http://127.0.0.1:8080"
    with TestClient(
        create_app(
            tokens_path=_pool(tmp_path / "tokens.json"),
            index_dir=tmp_path / "index",
            docs_parent_origin=parent,
        )
    ) as client:
        response = client.get("/docs")

    assert response.headers["content-security-policy"] == f"frame-ancestors {parent}"
    assert f'"{parent}"' in response.text
