"""The live HTTP surface: health, the error contract, and key authentication.

These tests pin what a participant — or the deploy script's smoke test, or a stranger who found the
host — sees. The three are one subject on purpose: the same middleware decides who gets in, and the
same helper builds every answer that says no.
"""

from __future__ import annotations

import logging
import re
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
from biohack_gateway.play import PLAY_PATH
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


def test_the_reference_opens_on_the_call_that_needs_no_key(client: TestClient) -> None:
    """The first group a reader meets is liveness, and it is first because of where it is declared.

    Swagger UI orders its groups by the order of `paths` in this document — its tag sorter keeps
    what it is given — so this test pins the reader's first impression, not a formatting preference.
    """
    paths = client.get("/openapi.json").json()["paths"]

    assert next(iter(paths)) == "/health"
    assert paths["/health"]["get"]["tags"] == ["liveness"]


def test_the_reference_does_not_read_its_padlocks_backwards(client: TestClient) -> None:
    """Swagger marks an authorized call with a closed padlock, which on this page says the opposite.

    This page is where a key is entered, so a closed padlock belongs on the call that still needs
    one. The two glyphs are exchanged in the page's stylesheet and the button's label is written to
    match, so what the icon shows and what a screen reader hears are the same thing.
    """
    page = client.get("/docs").text
    locked = re.search(r'svg\.locked path \{ d: path\("([^"]+)"\)', page)
    unlocked = re.search(r'svg\.unlocked path \{ d: path\("([^"]+)"\)', page)

    assert locked and unlocked, "the page does not exchange the two padlocks"
    assert "6 5.6V6h2v-.801" in locked.group(1)  # the open shackle belongs to the authorized state
    assert "M12 8H8V5.199" in unlocked.group(1)  # the closed one to the state that needs a key
    assert "this call sends your key" in page and "this call needs your key" in page


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


def test_the_reference_page_leaves_remembering_to_the_shared_store(client: TestClient) -> None:
    """Swagger UI is asked *not* to persist the key; the store the model page shares does that.

    Deliberately the opposite of what this page asked for until now. Two remembered copies of a
    secret cannot be cleared together, and the copy Swagger keeps is one the model page cannot
    clear — so the store is the only place a key is written, and both pages clear that one.
    """
    page = client.get("/docs").text

    assert '"persistAuthorization": false' in page
    assert "biohack-gateway/api-key" in page
    assert "apiKeyStore" in page


def test_both_framed_pages_share_one_store_for_the_key(client: TestClient) -> None:
    """One key, one place: set it on either page and the other uses it; clear it, both lose it."""
    reference = client.get("/docs").text
    play = client.get(PLAY_PATH).text

    for page in (reference, play):
        assert "biohack-gateway/api-key" in page
        assert "apiKeyStore.get()" in page
    # The model page offers the same two actions the reference does, because they act on one
    # key: one to use it, one to forget it everywhere.
    assert ">Use key<" in reference and ">Use key<" in play
    assert ">Forget<" in reference and ">Forget<" in play
    assert "apiKeyStore.clear()" in play


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


def test_the_model_page_answers_without_a_key(client: TestClient) -> None:
    """The page is where a key is entered, so it cannot itself need a header to be reachable."""
    assert client.get(PLAY_PATH).status_code == 200


def test_the_model_page_is_not_in_the_contract(client: TestClient) -> None:
    """It is a page for a browser, not an operation: the reference must not list it."""
    assert PLAY_PATH not in client.get("/openapi.json").json()["paths"]


def test_the_model_page_loads_the_site_own_decoder(client: TestClient) -> None:
    """One decoder, and it is the published one.

    The page draws a mask with the documentation site's `rle_decode.js` — the file the
    tutorial hands a participant to download — loaded by URL. A copy beside this module would
    be a second decoder to keep in step with the one the site promises is correct.
    """
    page = client.get(PLAY_PATH).text

    assert f'src="{DOCS_PARENT_ORIGIN}/post-processing/rle_decode.js"' in page
    assert "rleDecode" in page
    assert "function rleDecode" not in page  # the decoder is loaded, never copied


def test_the_model_page_names_no_model_and_no_parameter(client: TestClient) -> None:
    """The page is built from `GET /models`, so supporting a new model needs no change to it.

    This is the invariant the page exists to hold: a model it cannot render is an incomplete
    declaration rather than a page that is not clever enough. A model id or one of today's parameter
    names appearing in the page is that invariant broken, and it would be broken quietly.
    """
    page = client.get(PLAY_PATH).text

    assert "sam3.1" not in page
    for parameter in ("prompt", "confidence_threshold", "imgsz"):
        assert parameter not in page
    assert "/models/" in page and "/datasets/" in page  # it asks the surface instead


def test_the_model_page_reads_the_image_without_cors(client: TestClient) -> None:
    """`data.global-wheat.com` sends no `access-control-allow-origin`, so the image must not ask.

    An `<img>` carrying `crossorigin` would not load there at all, and the canvas it is drawn on is
    tainted either way — which is why the page composites the mask through an offscreen canvas and
    counts the decoded array rather than reading a pixel back.
    """
    page = client.get(PLAY_PATH).text

    assert "crossorigin=" not in page and "crossOrigin" not in page
    assert "getImageData" not in page
    assert "putImageData" in page


def test_the_model_page_may_only_be_framed_by_the_site(client: TestClient) -> None:
    """The key is typed into this page, so only the documentation site may embed it."""
    response = client.get(PLAY_PATH)

    assert response.headers["content-security-policy"] == f"frame-ancestors {DOCS_PARENT_ORIGIN}"
    assert f'"{DOCS_PARENT_ORIGIN}"' in response.text


def test_the_model_page_reports_its_height_to_the_same_site(tmp_path: Path) -> None:
    """Both framed pages share one reporter, and it names the origin the app was given."""
    parent = "http://127.0.0.1:8080"
    with TestClient(
        create_app(
            tokens_path=_pool(tmp_path / "tokens.json"),
            index_dir=tmp_path / "index",
            docs_parent_origin=parent,
        )
    ) as client:
        page = client.get(PLAY_PATH)

    assert page.headers["content-security-policy"] == f"frame-ancestors {parent}"
    assert f'"{parent}"' in page.text
    assert 'src="' + parent + '/post-processing/rle_decode.js"' in page.text
    assert 'SITE_ORIGIN = "' + parent + '"' in page.text


def test_the_model_page_maps_the_mask_into_canvas_order(client: TestClient) -> None:
    """A decoded mask is COCO's order; the canvas is row-major, and the two differ by a transpose.

    The decoder hands back an array that indexes as `row + column * height`, while `ImageData` is
    row-major — so copying the index straight across draws every mask lying down the image instead
    of across it. That looked plausible enough in a screenshot; it was caught by counting the drawn
    pixels and finding them outside the object's own box, which is why the mapping is pinned here.
    """
    page = client.get(PLAY_PATH).text

    assert "((index % height) * width + Math.floor(index / height)) * 4" in page
    # And the box is placed in the image's own fractions, so it stays on its object at any size the
    # pane gives the image. Nothing maps a scroll any more: the pane shows the whole photograph.
    assert "(x / image.naturalWidth) * 100" in page
    assert "scrollTop" not in page and "overflow-y: auto" not in page


def test_the_model_page_labels_each_box_with_its_score(client: TestClient) -> None:
    """The score is text over the raster, to two decimals, and never pixels inside it.

    The pane draws a photograph at whatever size fits in it — a 592×2509 row is a narrow strip — so
    a score painted into the bitmap would be a smudge there and a billboard on a wide crop. As an
    element it is the same size in both, and it is text a reader can select. Two decimals, because
    that is what a model's score is worth reading to.
    """
    page = client.get(PLAY_PATH).text

    assert "object.score.toFixed(2)" in page
    assert 'className = "mark"' in page
    # The box is a border on that element rather than a stroke in the bitmap.
    assert "strokeRect" not in page


def test_the_model_page_shows_the_same_call_as_a_command(client: TestClient) -> None:
    """What the controls send can be taken to a shell, and the key cannot be: it stays `$KEY`.

    The command is built from the controls rather than written down, so there is no second
    description
    of the surface to keep true; its origin is the page's own, which is the API's; and the one thing
    it must not carry is the key — a screenshot of this page, or a command line pasted into a chat,
    would otherwise carry a team's token.
    """
    page = client.get(PLAY_PATH).text

    assert "X-API-Key: $KEY" in page
    assert "location.origin + " in page
    assert "file=@" in page and "photo.jpg" in page
    # The command follows the controls, however many the model declares.
    assert 'parameters.addEventListener("input", refreshRequest)' in page


def test_the_model_page_orders_the_model_before_the_image_it_runs_on(client: TestClient) -> None:
    """A model, then the knobs it declares, then the image, and Run at the end of that last row.

    The parameters used to sit below the button that runs the model, on the far side of the image
    row, which read as a form with a stray line in it. Run belongs at the end of the last row — the
    reader sets everything to the left of it and presses it once.
    """
    page = client.get(PLAY_PATH).text

    assert page.index('id="model"') < page.index('id="parameters"') < page.index('id="run"')
    assert ">Parameters<" in page


def test_a_bounded_parameter_shows_the_number_it_is_set_to(client: TestClient) -> None:
    """A slider a reader cannot read is a slider they cannot set deliberately.

    The declaration carries the bounds and the default but no step, so the page picks one and shows
    the value with as many decimals as that step has places — `0.5` reads as `0.50` — and it names
    no parameter to do it.
    """
    page = client.get(PLAY_PATH).text

    assert 'createElement("output")' in page
    assert "toFixed(decimals)" in page
    assert 'input.type === "range"' in page


def test_the_model_page_says_what_came_back_once(client: TestClient) -> None:
    """The count and the Σ check are one line in the figure's caption, and that is the only one.

    It used to be two: a count beside the Run button, with the resolution repeated from the model
    row. The reader needs one place to look after pressing Run, and it is the caption of the
    picture the answer is drawn on.
    """
    page = client.get(PLAY_PATH).text

    assert 'tally + " · Σ decoded mask = "' in page
    assert 'say(state, "")' in page  # nothing is left beside the button once the run has answered


def test_the_reference_and_the_model_page_share_one_reporter(client: TestClient) -> None:
    """A second copy of a messaging contract is a second thing to keep true."""
    reference = client.get("/docs").text
    play = client.get(PLAY_PATH).text

    for page in (reference, play):
        assert page.count("const SITE_ORIGIN = ") == 1
    assert "docsHeight" in reference and "docsHeight" in play
    # Each page tells the reporter which box is the page, and the reference counts its key field.
    assert 'CONTAINER = "#swagger-ui"' in reference and 'EXTRA = "#key-bar"' in reference
    assert 'CONTAINER = "#play"' in play
