"""What ``predict_file`` puts on the wire: the side, the orientation, and the opt-out.

The fixtures are built here rather than committed, because the thing under test is what a phone
produces — a large JPEG with an orientation tag — and a file in the repository would be a second
thing to keep in step with it. Every call is answered by the stub session from ``test_client``.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, cast

from PIL import Image
from test_client import Reply, Session, make


def photo(
    width: int = 4000,
    height: int = 3000,
    orientation: int = 6,
    fmt: str = "JPEG",
) -> bytes:
    """Build a photograph with an EXIF orientation tag, as a camera writes one.

    Parameters
    ----------
    width, height : int
        The stored pixel size, before any orientation is applied.
    orientation : int
        The EXIF tag 0x0112 value: 6 is a portrait phone photo stored sideways, 1 is upright.
    fmt : str
        The Pillow format to encode as.

    Returns
    -------
    bytes
        The encoded file.
    """
    exif = Image.Exif()
    exif[0x0112] = orientation
    encoded = io.BytesIO()
    Image.new("RGB", (width, height), (110, 130, 90)).save(
        encoded, format=fmt, exif=exif, **({"quality": 85} if fmt == "JPEG" else {})
    )
    return encoded.getvalue()


def declaration(model: str, side: int, *, imgsz: bool = False) -> dict[str, Any]:
    """Build a ``GET /models/{id}`` body, reduced to what the client reads.

    Parameters
    ----------
    model : str
        The model id.
    side : int
        The ``resolution`` it publishes.
    imgsz : bool
        Whether it declares the ``imgsz`` parameter, as ``sam3.1-meta`` does and
        ``gwfss-segformer-b4`` does not.

    Returns
    -------
    dict
        The declaration.
    """
    parameters: list[dict[str, Any]] = []
    if imgsz:
        parameters.append(
            {"name": "imgsz", "type": "number", "values": [672, 784, 896, 1008], "default": 1008}
        )
    return {
        "id": model,
        "resolution": side,
        "declared": True,
        "parameters": parameters,
        "machines": [],
    }


def uploaded(session: Session) -> tuple[str, bytes, str]:
    """Read back the multipart part the client sent last.

    Parameters
    ----------
    session : Session
        The stub session the client used.

    Returns
    -------
    tuple of (str, bytes, str)
        The file name, the payload, and the media type.
    """
    return cast("tuple[str, bytes, str]", session.calls[-1]["files"]["file"])


def test_a_portrait_phone_photo_arrives_upright_and_at_the_model_s_side() -> None:
    """A 4000x3000 image tagged orientation 6 is a portrait; the model must not get it sideways."""
    original = photo()
    client, session = make(
        [
            Reply(200, declaration("sam3.1-meta", 1008, imgsz=True)),
            Reply(200, {"model": "sam3.1-meta", "objects": []}),
        ]
    )

    client.predict_file("sam3.1-meta", original)

    name, payload, media_type = uploaded(session)
    with Image.open(io.BytesIO(payload)) as sent:
        assert max(sent.size) <= 1008
        assert sent.height > sent.width, "the EXIF orientation was not applied"
        assert sent.getexif().get(0x0112) in (None, 1), "a rotated image must not stay tagged"
    assert (name, media_type) == ("upload.jpg", "image/jpeg")
    assert len(payload) < len(original)


def test_an_image_that_already_fits_is_sent_byte_for_byte() -> None:
    """A small JPEG keeps its quality, its metadata, and its exact bytes."""
    original = photo(width=800, height=600, orientation=1)
    client, session = make(
        [
            Reply(200, declaration("sam3.1-meta", 1008, imgsz=True)),
            Reply(200, {"model": "sam3.1-meta", "objects": []}),
        ]
    )

    client.predict_file("sam3.1-meta", original)

    assert uploaded(session)[1] is original


def test_resize_before_submit_false_sends_the_original_bytes_and_asks_nothing() -> None:
    """The opt-out is the escape hatch, and it costs no extra request."""
    original = photo()
    client, session = make([Reply(200, {"model": "sam3.1-meta", "objects": []})])

    client.predict_file("sam3.1-meta", original, resize_before_submit=False)

    assert len(session.calls) == 1
    assert len(uploaded(session)[1]) == len(original)
    sent = session.calls[0]["params"] or {}
    assert "resize_before_submit" not in sent


def test_imgsz_moves_the_target_and_still_travels_as_a_parameter() -> None:
    """The client's option is never forwarded; the model's own parameter still is."""
    client, session = make(
        [
            Reply(200, declaration("sam3.1-meta", 1008, imgsz=True)),
            Reply(200, {"model": "sam3.1-meta", "objects": []}),
        ]
    )

    client.predict_file("sam3.1-meta", photo(), imgsz=504)

    with Image.open(io.BytesIO(uploaded(session)[1])) as sent:
        assert max(sent.size) == 504
    assert session.calls[-1]["params"] == {"imgsz": 504}


def test_a_model_that_publishes_512_gets_512_not_1008(tmp_path: Path) -> None:
    """The side belongs to the model: segformer resizes to 512 itself, so 512 is what travels."""
    original = tmp_path / "photo.png"
    original.write_bytes(photo(width=2000, height=1500, orientation=1, fmt="PNG"))
    client, session = make(
        [
            Reply(200, declaration("gwfss-segformer-b4", 512)),
            Reply(200, {"model": "gwfss-segformer-b4", "objects": []}),
        ]
    )

    client.predict_file("gwfss-segformer-b4", original)

    name, payload, media_type = uploaded(session)
    with Image.open(io.BytesIO(payload)) as sent:
        assert max(sent.size) == 512
    assert (name, media_type) == ("photo.jpg", "image/jpeg")


def test_the_declaration_is_fetched_once_per_client() -> None:
    """Two uploads to one model ask what it runs at once, not twice."""
    client, session = make(
        [
            Reply(200, declaration("sam3.1-meta", 1008, imgsz=True)),
            Reply(200, {"model": "sam3.1-meta", "objects": []}),
            Reply(200, {"model": "sam3.1-meta", "objects": []}),
        ]
    )

    client.predict_file("sam3.1-meta", photo())
    client.predict_file("sam3.1-meta", photo())

    declared = [
        call for call in session.calls if call["url"] == "https://api.test/models/sam3.1-meta"
    ]
    assert len(declared) == 1


def test_a_declaration_that_fails_leaves_the_original_alone() -> None:
    """The predict call reports the failure; this one must not invent a second error."""
    original = photo()
    refusal = {"error": {"code": "invalid_key", "message": "that key is not in the pool"}}
    client, session = make(
        [
            Reply(401, refusal),
            Reply(200, {"model": "sam3.1-meta", "objects": []}),
        ]
    )

    client.predict_file("sam3.1-meta", original)

    assert uploaded(session)[1] is original
