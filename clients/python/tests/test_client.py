"""The client's own behaviour: address building, paging, parameters, and refusals.

Every call is answered by a stub session, so what these tests check is the request this file builds
and the way it reads an answer — not the server. The one test that needs the server is in
``test_live.py`` and is skipped unless it is asked for.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from rle_fixture import encode

import global_wheat
from global_wheat import ApiError, Client


class Reply:
    """One answer, reduced to what the client reads from it."""

    def __init__(self, status: int, body: Any, text: str = "") -> None:
        self.status_code = status
        self._body = body
        self.text = text
        self.content = text.encode()
        self.reason = "stub"

    def json(self) -> Any:
        """Answer the decoded body, or raise where the server sent something else."""
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Session:
    """A session that answers from a list and records what it was asked."""

    def __init__(self, replies: Sequence[Reply]) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self._replies = list(replies)

    def request(self, method: str, url: str, **kwargs: Any) -> Reply:
        """Record one call and answer it from the list."""
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._replies.pop(0)

    def close(self) -> None:
        """Record that the session was closed."""
        self.calls.append({"method": "CLOSE", "url": ""})


def make(replies: Sequence[Reply]) -> tuple[Client, Session]:
    """Build a client whose transport is a stub.

    Parameters
    ----------
    replies : sequence of Reply
        The answers, in the order the test expects the calls.

    Returns
    -------
    tuple of (Client, Session)
        The client and the session it is using, so a test can read the calls back.
    """
    session = Session(replies)
    return Client(key="t0ken", base="https://api.test", session=session), session


def test_a_client_without_a_key_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The failure names both ways to supply one, rather than arriving as a 401 later."""
    monkeypatch.delenv(global_wheat.KEY_ENV, raising=False)
    with pytest.raises(ValueError, match="GLOBAL_WHEAT_API_KEY"):
        Client()


def test_key_and_base_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A notebook sets two variables and passes nothing."""
    monkeypatch.setenv(global_wheat.KEY_ENV, "env-token")
    monkeypatch.setenv(global_wheat.BASE_ENV, "https://api.example/")
    client = Client(session=Session([]))
    assert client.key == "env-token"
    assert client.base == "https://api.example"


def test_datasets_unwraps_the_directory() -> None:
    """The body's one key is not something a caller should have to remember."""
    client, session = make([Reply(200, {"datasets": [{"dataset": "gwhd"}]})])
    assert client.datasets() == [{"dataset": "gwhd"}]
    assert session.calls[0]["url"] == "https://api.test/datasets"
    assert session.headers == {global_wheat.API_KEY_HEADER: "t0ken"}


def test_iter_images_follows_the_cursor_and_keeps_the_filters() -> None:
    """A loop sees rows, not pages; the filter pairs ride along on every request."""
    client, session = make(
        [
            Reply(200, {"images": [{"row": 1}, {"row": 2}], "next_cursor": "c1"}),
            Reply(200, {"images": [{"row": 3}], "next_cursor": None}),
        ]
    )
    rows = [row["row"] for row in client.iter_images("gwhd", split="train", limit=2)]
    assert rows == [1, 2, 3]
    assert session.calls[0]["params"] == {"limit": 2, "split": "train"}
    assert session.calls[1]["params"] == {"limit": 2, "split": "train", "cursor": "c1"}


def test_describe_asks_once() -> None:
    """The palette is a session fact, and label_masks needs it on every call."""
    client, session = make([Reply(200, {"dataset": "gwfss", "classes": []})])
    client.describe("gwfss")
    client.describe("gwfss")
    assert len(session.calls) == 1


def test_a_refusal_becomes_an_api_error_with_the_api_s_own_words() -> None:
    """The contract's one error body is the one an exception carries."""
    body = {"error": {"code": "invalid_key", "message": "that key is not in the pool"}}
    client, _ = make([Reply(401, body)])
    with pytest.raises(ApiError) as caught:
        client.models()
    assert (caught.value.status, caught.value.code) == (401, "invalid_key")
    assert caught.value.message == "that key is not in the pool"
    assert "invalid_key" in str(caught.value)


def test_a_body_outside_the_contract_still_raises() -> None:
    """A proxy's HTML page is not a reason to return a dict that is not a model directory."""
    client, _ = make([Reply(502, ValueError("not json"), text="<html>bad gateway</html>")])
    with pytest.raises(ApiError) as caught:
        client.models()
    assert caught.value.status == 502
    assert caught.value.code == "internal_error"
    assert "bad gateway" in caught.value.message


def test_an_image_comes_back_with_its_masks() -> None:
    """``mask`` is there without being asked for, and it is the array the answer describes."""
    flat = [False] * 9
    flat[0] = True
    flat[4] = True
    body = {
        "dataset": "gwhd",
        "row": 0,
        "objects": [
            {
                "idx": 0,
                "category": "head",
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "area": 2.0,
                "rle": {"size": [3, 3], "counts": encode(flat)},
            }
        ],
    }
    client, _ = make([Reply(200, body)])
    decoded = client.image("gwhd", 0)
    mask = decoded["objects"][0]["mask"]
    assert mask.shape == (3, 3)
    assert int(mask.sum()) == 2
    assert decoded["objects"][0]["rle"]["counts"] == encode(flat)  # the raw entry is kept


def test_decode_false_keeps_the_answer_as_written() -> None:
    """A row with dozens of objects is allowed to cost nothing to read."""
    body = {"objects": [{"idx": 0, "rle": {"size": [1, 1], "counts": "0"}}]}
    client, _ = make([Reply(200, body)])
    assert "mask" not in client.image("gwhd", 0, decode=False)["objects"][0]


def test_predict_sends_the_address_in_the_body_and_the_parameters_in_the_query() -> None:
    """Parameters are the model's own and are forwarded verbatim, under their own names."""
    client, session = make([Reply(200, {"model": "sam3.1-meta", "objects": []})])
    client.predict("sam3.1-meta", "gwhs-temp", 7, prompt="wheat head", confidence_threshold=0.4)
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.test/models/sam3.1-meta/predict"
    assert call["json"] == {"dataset": "gwhs-temp", "row": 7}
    assert call["params"] == {"prompt": "wheat head", "confidence_threshold": 0.4}


def test_an_upload_sends_the_file_as_the_field_named_file(tmp_path: Path) -> None:
    """The route's contract is one multipart field, and the name and type are only what it needs."""
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\n")
    client, session = make([Reply(200, {"model": "sam3.1-meta", "objects": []})])
    client.predict_file("sam3.1-meta", photo, prompt="head")
    name, payload, media_type = session.calls[0]["files"]["file"]
    assert (name, payload, media_type) == ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")
    assert session.calls[0]["url"].endswith("/predict/upload")


def test_a_geometry_parameter_is_one_json_value_in_one_parameter() -> None:
    """`boxes` is an array of boxes, not five parameters named `boxes`."""
    client, session = make([Reply(200, {"model": "sam3.1-meta", "objects": []})])
    client.predict(
        "sam3.1-meta", "gwhs-temp", 7, prompt="wheat head", boxes=[[480, 290, 110, 360, 1]]
    )

    assert session.calls[0]["params"] == {
        "prompt": "wheat head",
        "boxes": "[[480,290,110,360,1]]",
    }


def test_an_upload_encodes_a_geometry_parameter_the_same_way() -> None:
    """One encoder, both routes: the upload carries the same query contract as the address call."""
    client, session = make([Reply(200, {"model": "sam3.1-meta", "objects": []})])
    client.predict_file(
        "sam3.1-meta", b"\x89PNG\r\n\x1a\n", points=[[256, 300, 1], [40, 40, 0]]
    )

    assert session.calls[0]["params"] == {"points": "[[256,300,1],[40,40,0]]"}


def test_a_boolean_is_true_or_false_and_an_unset_value_is_left_out() -> None:
    """`True` is not what the API reads, and a `None` is the caller saying nothing."""
    client, session = make([Reply(200, {"model": "sam3.1-meta", "objects": []})])
    client.predict("sam3.1-meta", "gwhs-temp", 7, recursive=True, prompt=None)

    assert session.calls[0]["params"] == {"recursive": "true"}


def test_the_filter_repetition_stays_on_the_dataset_routes() -> None:
    """`?split=train&split=val` narrows a listing, so that convention must not follow `boxes`."""
    client, session = make([Reply(200, {"images": [], "next_cursor": None})])
    client.images("gwhd", split=["train", "val"])

    assert session.calls[0]["params"] == {"split": ["train", "val"], "limit": 100}


def test_the_key_is_not_sent_to_the_image_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    """``data.*`` is public, and a token in a CDN's logs is a token leaked."""
    seen: dict[str, Any] = {}

    def get(url: str, timeout: float) -> Reply:
        seen.update(url=url, timeout=timeout)
        return Reply(200, {}, text="bytes")

    monkeypatch.setattr(global_wheat.requests, "get", get)
    client, _ = make([])
    client.fetch("https://data.example/gwhd/images/0.jpg")
    assert seen["url"] == "https://data.example/gwhd/images/0.jpg"


def test_label_masks_uses_the_dataset_s_palette() -> None:
    """One description fetch, then the split; no palette has to be passed by the caller."""
    classes = [{"id": 0, "name": "head", "color": "#32ff84"}]
    client, session = make([Reply(200, {"dataset": "gwfss", "classes": classes})])
    import numpy as np

    pixels = np.zeros((1, 1, 3), dtype=np.uint8)
    pixels[0, 0] = (0x32, 0xFF, 0x84)
    assert client.label_masks(pixels, "gwfss")["head"][0, 0]
    assert len(session.calls) == 1
