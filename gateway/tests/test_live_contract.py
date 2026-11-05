"""The deployed API, called the way the site tells a participant to call it.

Skipped unless ``GLOBAL_WHEAT_LIVE=1`` and a participant token is in ``GLOBAL_WHEAT_API_KEY``: it
needs the network, a key and the pool. ``GLOBAL_WHEAT_API`` points it at another host. Online tests
in ``tests/`` use fakes; this file is the release check, and every assertion comes from a
sentence on <https://docs.global-wheat.com>.

    GLOBAL_WHEAT_LIVE=1 GLOBAL_WHEAT_API_KEY=... \
        uv run --project gateway pytest tests/test_live_contract.py

Paid generations are behind ``GLOBAL_WHEAT_LIVE_GENERATE=1``; the 16 MB upload behind
``GLOBAL_WHEAT_LIVE_BIG_UPLOAD=1``. Both need no machine in the pool because they spend money or
bandwidth rather than a card.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("GLOBAL_WHEAT_LIVE") != "1",
    reason="set GLOBAL_WHEAT_LIVE=1 and GLOBAL_WHEAT_API_KEY to call the API",
)

API = os.environ.get("GLOBAL_WHEAT_API", "https://api.global-wheat.com").rstrip("/")
KEY = {"X-API-Key": os.environ.get("GLOBAL_WHEAT_API_KEY", "")}
DATA = "https://data.global-wheat.com"
FIXTURE = Path(__file__).resolve().parents[2] / "docs/scripts/fixtures/model-directory.json"

GWH_IDS = ["gwhd", "gwfss", "gwhs-temp"]
"""The datasets a participant may call; `gwhs` is not served until its annotation is released."""


@pytest.fixture(scope="session")
def client() -> Any:
    """One authenticated connection to the host under test."""
    if not KEY["X-API-Key"]:
        pytest.skip("GLOBAL_WHEAT_API_KEY is not set")
    with httpx.Client(base_url=API, headers=KEY, timeout=300.0) as session:
        yield session


@pytest.fixture(scope="session")
def models(client: httpx.Client) -> dict[str, dict[str, Any]]:
    """Read the model directory, by id, as the host publishes it once."""
    return {entry["id"]: entry for entry in client.get("/models").json()["models"]}


@pytest.fixture(scope="session")
def photograph() -> bytes:
    """One published photograph, for the upload tests."""
    return httpx.get(f"{DATA}/gwhd/images/00000000.jpg", timeout=120.0).content


@pytest.fixture(scope="session")
def carrot() -> bytes:
    """Fetch the public example photograph a detector finds carrots in."""
    return httpx.get(f"{DATA}/dev/carrot.jpg", timeout=120.0).content


def require(models: dict[str, dict[str, Any]], model: str) -> dict[str, Any]:
    """Skip a test when a model has no machine answering, which is not a contract failure."""
    entry = models.get(model)
    if entry is None:
        pytest.fail(f"{model} is not in the directory")
    if not entry.get("backends_up"):
        pytest.skip(f"{model} has no machine answering")
    return entry


def picture(body: dict[str, Any]) -> dict[str, Any]:
    """Decode the picture an answer carried and check its bytes."""
    image = body.get("image")
    assert image, "the answer carried no image"
    raw = base64.b64decode(image["data"])
    assert len(raw) > 10_000, f"a {len(raw)}-byte picture is not one"
    assert image["media_type"] in ("image/png", "image/jpeg")
    assert image["width"] > 0 and image["height"] > 0
    return {**image, "bytes": len(raw)}


# --------------------------------------------------------------------------- the gate and the pages


def test_the_public_pages_answer_without_a_key() -> None:
    """`/health`, `/docs`, `/play` and the contract are the four anonymous paths."""
    for path in ("/health", "/docs", "/play", "/openapi.json"):
        answer = httpx.get(API + path, timeout=30.0)
        assert answer.status_code == 200, path

    health = httpx.get(API + "/health", timeout=30.0).json()
    assert health["status"] == "ok" and "version" in health


def test_everything_else_needs_a_key() -> None:
    """The gate is the default: an unknown path is refused before it is routed."""
    for path in ("/", "/datasets", "/models", "/no/such/route"):
        answer = httpx.get(API + path, timeout=30.0)
        assert answer.status_code == 401, path
        assert answer.json()["error"]["code"] == "invalid_key"


def test_a_key_in_the_query_is_not_a_key(client: httpx.Client) -> None:
    """The key travels in `X-API-Key` and nowhere else."""
    answer = httpx.get(f"{API}/datasets", params={"X-API-Key": KEY["X-API-Key"]}, timeout=30.0)
    assert answer.status_code == 401


def test_a_wrong_key_is_refused() -> None:
    """A token nobody issued is `401`, not a 404 or a 500."""
    answer = httpx.get(API + "/datasets", headers={"X-API-Key": "not-a-key"}, timeout=30.0)
    assert answer.status_code == 401
    assert answer.json()["error"]["code"] == "invalid_key"


def test_the_contract_lists_every_participant_route(client: httpx.Client) -> None:
    """The reference is generated from these routes, so a missing one is a missing page."""
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/health",
        "/datasets",
        "/datasets/{dataset}",
        "/datasets/{dataset}/images",
        "/datasets/{dataset}/images/{row}",
        "/models",
        "/models/{model}",
        "/models/{model}/predict",
        "/models/{model}/predict/upload",
        "/models/{model}/generate",
    } == paths


# --------------------------------------------------------------------------- datasets


def test_the_directory_is_the_published_datasets(client: httpx.Client) -> None:
    """Three ids, and the row counts the site prints."""
    body = client.get("/datasets").json()
    rows = {entry["id"]: entry["rows"] for entry in body["datasets"]}
    assert rows == {"gwhd": 6512, "gwfss": 1096, "gwhs-temp": 10}


def test_an_unreleased_dataset_is_not_found(client: httpx.Client) -> None:
    """`gwhs` has a page but no published index; a call for it is a 404."""
    answer = client.get("/datasets/gwhs")
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("dataset", GWH_IDS)
def test_a_dataset_describes_itself(client: httpx.Client, dataset: str) -> None:
    """The description carries its provenance, splits and facets."""
    body = client.get(f"/datasets/{dataset}").json()
    assert body["dataset"] == dataset
    assert body["rows"] > 0
    assert sum(body["splits"].values()) == body["rows"]
    assert body["filters"] and all(isinstance(values, dict) for values in body["filters"].values())


def test_a_page_walks_every_row_once(client: httpx.Client) -> None:
    """The cursor resumes where the last page ended, and the walk ends."""
    seen: list[int] = []
    cursor = None
    for _ in range(10):
        params = {"limit": 1000}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/datasets/gwhd/images", params=params).json()
        assert body["count"] == len(body["images"])
        seen.extend(item["row"] for item in body["images"])
        cursor = body["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == list(range(6512))


@pytest.mark.parametrize("limit", ["0", "1001", "-1", "abc"])
def test_a_page_limit_outside_the_range_is_refused(client: httpx.Client, limit: str) -> None:
    """The page is bounded at both ends; either violation is the contract's 400."""
    answer = client.get("/datasets/gwhd/images", params={"limit": limit})
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "invalid_request"


def test_a_cursor_that_is_not_one_is_refused(client: httpx.Client) -> None:
    """A cursor is the surface's own or nothing."""
    answer = client.get("/datasets/gwhd/images", params={"cursor": "not-a-cursor"})
    assert answer.status_code == 400


def test_a_filter_narrows_and_two_values_intersect(client: httpx.Client) -> None:
    """A repeated key is an `and`: both values must hold for a row to be selected."""
    page = client.get("/datasets/gwhd/images", params={"split": "train", "limit": 20}).json()
    assert page["images"] and {item["split"] for item in page["images"]} == {"train"}

    both = client.get(
        "/datasets/gwhs-temp/images", params=[("tags", "Labo"), ("tags", "healthy")]
    ).json()
    one = client.get("/datasets/gwhs-temp/images", params=[("tags", "Labo")]).json()
    assert 0 < both["count"] < one["count"]

    impossible = client.get(
        "/datasets/gwhd/images", params=[("split", "train"), ("split", "validation")]
    ).json()
    assert impossible["count"] == 0


def test_an_unknown_filter_key_is_an_empty_page(client: httpx.Client) -> None:
    """A key the dataset does not have selects nothing rather than refusing the request."""
    answer = client.get("/datasets/gwhd/images", params={"colour": "red"})
    assert answer.status_code == 200
    assert answer.json()["count"] == 0


def test_one_image_carries_its_whole_annotation(client: httpx.Client) -> None:
    """The three shapes: boxes, a palette mask, objects with RLE."""
    boxes = client.get("/datasets/gwhd/images/0").json()
    assert len(boxes["objects"]) == 144, "the number the gwhd page prints"
    for obj in boxes["objects"]:
        x, y, width, height = obj["bbox"]
        assert x >= 0 and y >= 0 and x + width <= boxes["width"] + 0.5
        assert y + height <= boxes["height"] + 0.5

    semantic = client.get("/datasets/gwfss/images/0").json()
    assert semantic["mask_url"] and "objects" not in semantic

    instances = client.get("/datasets/gwhs-temp/images/0").json()
    assert len(instances["objects"]) == 24
    for obj in instances["objects"]:
        assert obj["rle"]["size"] == [instances["height"], instances["width"]]
        assert obj["area"] > 0 and obj["bbox"]


def test_a_row_that_does_not_exist_is_not_found(client: httpx.Client) -> None:
    """Past the end is a miss, not a bad request."""
    for path in ("/datasets/gwhd/images/6512", "/datasets/gwhd/images/-1"):
        answer = client.get(path)
        assert answer.status_code == 404, path
        assert answer.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("row", ["abc", "1.5", "null"])
def test_a_row_that_is_not_a_number_is_refused(client: httpx.Client, row: str) -> None:
    """The address is an integer; anything else is the request's fault."""
    answer = client.get(f"/datasets/gwhd/images/{row}")
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "invalid_request"


def test_image_and_mask_urls_are_public_and_immutable(client: httpx.Client) -> None:
    """Dataset bytes bypass the gateway and need no key; the cache policy is part of the object."""
    for dataset, want in (("gwhd", "image/jpeg"), ("gwfss", "image/png")):
        detail = client.get(f"/datasets/{dataset}/images/0").json()
        for field in ("image_url", "mask_url"):
            url = detail.get(field)
            if not url:
                continue
            answer = httpx.get(url, timeout=120.0)  # deliberately no key
            assert answer.status_code == 200, url
            assert want in answer.headers["content-type"]
            assert "immutable" in answer.headers["cache-control"]
            assert len(answer.content) > 1000


# --------------------------------------------------------------------------- the model directory


def test_the_directory_lists_every_documented_model(client: httpx.Client) -> None:
    """Thirteen ids, each with the shape the site describes."""
    body = client.get("/models").json()
    ids = {entry["id"] for entry in body["models"]}
    assert len(ids) == 13
    for entry in body["models"]:
        assert "resolution" in entry and "parameters" in entry
        assert "backends_up" in entry and "checked_seconds_ago" in entry


def test_one_model_is_the_entry_the_directory_carries(client: httpx.Client) -> None:
    """A detail is the directory's line, re-read."""
    entry = client.get("/models/sam3.1-meta").json()
    assert entry["id"] == "sam3.1-meta"
    assert [parameter["name"] for parameter in entry["parameters"]] == [
        "prompt",
        "boxes",
        "points",
        "confidence_threshold",
        "imgsz",
    ]


def test_an_unknown_model_is_not_found(client: httpx.Client) -> None:
    """An id nobody configured is a 404."""
    answer = client.get("/models/no-such-model")
    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "model",
    [
        "dinov3-vitl16",
        "dinov3-vith16plus",
        "gwfss-segformer-b4",
        "internvl-u",
        "nano-banana-2",
        "nano-banana-2.1",
        "nano-banana-pro",
        "sam3.1-meta",
        "sprout-l-gwfss",
        "tasselnetv4",
        "wevisone-vl-4b",
        "yolo26-seg-ultralytics",
        "yolo26-ultralytics",
    ],
)
def test_the_docs_fixture_matches_the_live_declaration(
    client: httpx.Client, models: dict[str, dict[str, Any]], model: str
) -> None:
    """The parameter table on a model page is generated from this record, not from prose."""
    recorded = {entry["id"]: entry for entry in json.loads(FIXTURE.read_text())["models"]}[model]
    live = models[model]
    if not live.get("backends_up"):
        pytest.skip(f"{model} has nothing declared while no machine answers")
    assert recorded["resolution"] == live["resolution"]
    assert [parameter["name"] for parameter in recorded["parameters"]] == [
        parameter["name"] for parameter in live["parameters"]
    ]


# --------------------------------------------------------------------------- each answer shape


def test_a_promptable_segmenter_answers_instances(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """A phrase and a row answer objects with a box, an area and a COCO mask."""
    require(models, "sam3.1-meta")
    detail = client.get("/datasets/gwhs-temp/images/0").json()
    body = client.post(
        "/models/sam3.1-meta/predict",
        params={"prompt": "wheat head"},
        json={"dataset": "gwhs-temp", "row": 0},
    ).json()
    assert body["model"] == "sam3.1-meta" and body["objects"]
    for obj in body["objects"]:
        assert obj["rle"]["size"] == [detail["height"], detail["width"]]
        assert obj["area"] > 0 and obj["bbox"][2] > 0 and obj["bbox"][3] > 0


@pytest.mark.parametrize("model", ["gwfss-segformer-b4", "sprout-l-gwfss"])
def test_a_semantic_segmenter_answers_one_object_per_class(
    client: httpx.Client, models: dict[str, dict[str, Any]], model: str
) -> None:
    """No instances and no background: one mask per nonempty foreground class."""
    require(models, model)
    palette = {entry["name"] for entry in client.get("/datasets/gwfss").json()["classes"]}
    body = client.post(f"/models/{model}/predict", json={"dataset": "gwfss", "row": 0}).json()
    assert body["objects"] and {obj["category"] for obj in body["objects"]} <= palette
    assert "background" not in {obj["category"] for obj in body["objects"]}


def test_a_counter_answers_a_count_and_the_field_it_integrates(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """`objects` is empty; `count` is the sum of the density grid."""
    require(models, "tasselnetv4")
    body = client.post(
        "/models/tasselnetv4/predict",
        params={"boxes": json.dumps([[306, 148, 55, 44, 1], [487, 313, 37, 70, 1]])},
        json={"dataset": "gwhd", "row": 4},
    ).json()
    assert body["objects"] == []
    density = body["density"]
    assert len(density["values"]) == density["size"][0] * density["size"][1]
    assert abs(sum(density["values"]) - body["count"]) < 0.05 * max(1.0, body["count"])


@pytest.mark.parametrize(("model", "dim"), [("dinov3-vitl16", 1024), ("dinov3-vith16plus", 1280)])
def test_a_backbone_answers_its_tokens(
    client: httpx.Client, models: dict[str, dict[str, Any]], model: str, dim: int
) -> None:
    """A CLS vector and a patch grid in base64 float16, with no objects."""
    require(models, model)
    body = client.post(f"/models/{model}/predict", json={"dataset": "gwfss", "row": 0}).json()
    assert body["objects"] == [] and len(body["embedding"]) == dim
    features = body["features"]
    assert features["dim"] == dim and features["dtype"] == "float16"
    rows, columns = features["size"]
    raw = base64.b64decode(features["data"])
    assert len(raw) == rows * columns * dim * 2, "one little-endian float16 per value"


def test_a_detector_finds_nothing_on_a_wheat_plot(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """The yolo page says a gwhd image answers an empty list; COCO knows no wheat."""
    require(models, "yolo26-ultralytics")
    body = client.post(
        "/models/yolo26-ultralytics/predict", json={"dataset": "gwhd", "row": 0}
    ).json()
    assert body["objects"] == [] and body["resolution_used"] == 1280


@pytest.mark.parametrize(
    ("model", "masked"),
    [("yolo26-ultralytics", False), ("yolo26-seg-ultralytics", True)],
)
def test_a_detector_finds_the_example_photograph(
    client: httpx.Client,
    models: dict[str, dict[str, Any]],
    carrot: bytes,
    model: str,
    masked: bool,
) -> None:
    """The dev photographs are the documented example for a COCO checkpoint."""
    require(models, model)
    body = client.post(
        f"/models/{model}/predict/upload",
        params={"confidence_threshold": 0.25},
        files={"file": ("carrot.jpg", carrot, "image/jpeg")},
    ).json()
    assert body["objects"] and {obj["category"] for obj in body["objects"]} == {"carrot"}
    assert all(("rle" in obj) is masked for obj in body["objects"])


def test_an_open_vocabulary_model_uses_the_phrase_as_the_class(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """`category` is the prompt, and there is no score."""
    require(models, "wevisone-vl-4b")
    body = client.post(
        "/models/wevisone-vl-4b/predict",
        params={"prompt": "wheat head"},
        json={"dataset": "gwhd", "row": 0},
    ).json()
    assert body["objects"]
    assert {obj["category"] for obj in body["objects"]} == {"wheat head"}
    assert all("score" not in obj for obj in body["objects"])


def test_a_vision_language_model_answers_prose_and_pictures(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """`mode` chooses prose, a picture, or both, and `objects` stays empty."""
    require(models, "internvl-u")
    prose = client.post(
        "/models/internvl-u/predict",
        params={"prompt": "Describe this image.", "mode": "text"},
        json={"dataset": "gwhs-temp", "row": 0},
    ).json()
    assert prose["objects"] == [] and len(prose["answer"]) > 0

    drawn = client.post(
        "/models/internvl-u/predict",
        params={"prompt": "A wheat spike on dark soil.", "mode": "image", "size": 512},
        json={"dataset": "gwhd", "row": 0},
    ).json()
    assert drawn["objects"] == [] and drawn["resolution_used"] == 512
    assert picture(drawn)["width"] <= 512 and picture(drawn)["height"] <= 512


@pytest.mark.skipif(
    os.environ.get("GLOBAL_WHEAT_LIVE_GENERATE") != "1",
    reason="a generation is a paid sample; set GLOBAL_WHEAT_LIVE_GENERATE=1 to run it",
)
def test_a_hosted_picture_model_generates_and_edits(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """A prompt alone makes a picture; an edit of a row carries the frame."""
    require(models, "nano-banana-2.1")
    generated = client.post(
        "/models/nano-banana-2.1/generate",
        params={"prompt": "a single wheat spike against dark soil", "image_size": "1K"},
    ).json()
    assert generated["objects"] == [] and picture(generated)

    edited = client.post(
        "/models/nano-banana-2.1/predict",
        params={"prompt": "make the background white", "aspect_ratio": "auto", "image_size": "1K"},
        json={"dataset": "gwhs-temp", "row": 0},
    ).json()
    frame = edited.get("frame")
    assert frame and frame["width"] > 0 and frame["height"] > 0
    left, top, right, bottom = frame["content"]
    assert 0 <= left < right <= 1 and 0 <= top < bottom <= 1


# --------------------------------------------------------------------------- parameters and bodies


@pytest.mark.parametrize(
    ("model", "params", "status", "code"),
    [
        ("sam3.1-meta", {"prompt": "wheat head", "nonsense": 1}, 400, "invalid_request"),
        ("sam3.1-meta", [("prompt", "a"), ("prompt", "b")], 400, "invalid_request"),
        ("sam3.1-meta", {"prompt": "x", "boxes": "not json"}, 400, "invalid_request"),
        ("sam3.1-meta", {"prompt": "x", "boxes": "[[1,2,3]]"}, 400, "invalid_request"),
        ("sam3.1-meta", {"prompt": "x", "boxes": "[[1,2,3,4,2]]"}, 400, "invalid_request"),
        ("sam3.1-meta", {"prompt": "x", "points": "[[1,2,5]]"}, 400, "invalid_request"),
        (
            "sam3.1-meta",
            {"prompt": "wheat head", "confidence_threshold": 1.5},
            400,
            "invalid_request",
        ),
        ("sam3.1-meta", {"prompt": "wheat head", "imgsz": 100}, 400, "invalid_request"),
        ("sam3.1-meta", {"prompt": "x" * 201}, 400, "invalid_request"),
        ("tasselnetv4", {}, 400, "invalid_request"),
        ("yolo26-ultralytics", {"imgsz": 320}, 400, "invalid_request"),
        ("wevisone-vl-4b", {"prompt": "x", "max_instances": 101}, 400, "invalid_request"),
    ],
)
def test_the_parameter_contract_is_the_model_s(
    client: httpx.Client,
    models: dict[str, dict[str, Any]],
    model: str,
    params: Any,
    status: int,
    code: str,
) -> None:
    """A name, a value or a shape the declaration does not allow is `400`, before a machine."""
    require(models, model)
    answer = client.post(
        f"/models/{model}/predict",
        params=params,
        json={"dataset": "gwhs-temp", "row": 0},
    )
    assert answer.status_code == status
    assert answer.json()["error"]["code"] == code


def test_a_geometry_label_may_be_left_out(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """The site says a label defaults to positive; a four-number box is accepted."""
    require(models, "sam3.1-meta")
    answer = client.post(
        "/models/sam3.1-meta/predict",
        params={"prompt": "wheat head", "boxes": "[[10, 20, 100, 100]]"},
        json={"dataset": "gwhs-temp", "row": 0},
    )
    assert answer.status_code == 200


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"row": 0}, 400),
        ({"dataset": "gwhd"}, 400),
        ({"dataset": "gwhs", "row": 0}, 404),
        ({"dataset": "../gwhd", "row": 0}, 404),
        ({"dataset": "gwhd", "row": 6512}, 404),
        ({"dataset": "gwhd", "row": -1}, 404),
        ({"dataset": "gwhd", "row": "abc"}, 400),
    ],
)
def test_the_address_is_resolved_through_the_index(
    client: httpx.Client, payload: dict[str, Any], status: int
) -> None:
    """A dataset id and a row, never a URL and never a path the request builds."""
    answer = client.post("/models/sam3.1-meta/predict", json=payload)
    assert answer.status_code == status
    assert answer.json()["error"]["code"] in ("invalid_request", "not_found")


def test_a_body_that_is_not_json_is_refused(client: httpx.Client) -> None:
    """The body is one small JSON object; another type is refused with the contract's body."""
    for content, content_type in ((b"{not json", "application/json"), (b"{}", "text/plain")):
        answer = client.post(
            "/models/sam3.1-meta/predict",
            content=content,
            headers={"Content-Type": content_type},
        )
        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "invalid_request"


def test_a_body_over_four_kilobytes_is_refused(client: httpx.Client) -> None:
    """The prediction body has a ceiling, and it is `413`."""
    answer = client.post(
        "/models/sam3.1-meta/predict",
        json={"dataset": "gwhd", "row": 0, "pad": "x" * 5000},
    )
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "payload_too_large"


def test_a_method_a_route_does_not_have_keeps_the_shape(client: httpx.Client) -> None:
    """A `405` is still the contract's error body."""
    answer = client.get("/models/sam3.1-meta/predict")
    assert answer.status_code == 405
    assert answer.json()["error"]["code"] == "invalid_request"


# --------------------------------------------------------------------------- uploads


def test_an_upload_without_a_file_is_refused(client: httpx.Client) -> None:
    """The bytes are the request; a form without them names nothing to run."""
    answer = client.post("/models/sam3.1-meta/predict/upload", params={"prompt": "wheat head"})
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "invalid_request"


def test_an_upload_under_another_field_name_is_refused(
    client: httpx.Client, photograph: bytes
) -> None:
    """`file` is the field the contract names."""
    answer = client.post(
        "/models/sam3.1-meta/predict/upload",
        files={"image": ("photo.jpg", photograph, "image/jpeg")},
    )
    assert answer.status_code == 400


def test_an_empty_or_non_image_upload_is_refused(client: httpx.Client) -> None:
    """The type is decided by the bytes, and an empty part is refused before it is read."""
    for payload in (b"", b"not an image at all"):
        answer = client.post(
            "/models/sam3.1-meta/predict/upload",
            params={"prompt": "wheat head"},
            files={"file": ("photo.jpg", payload, "image/jpeg")},
        )
        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "invalid_request"


def test_an_upload_answers(
    client: httpx.Client, models: dict[str, dict[str, Any]], photograph: bytes
) -> None:
    """A photograph goes through the gateway as bytes and comes back as the model's answer."""
    require(models, "sam3.1-meta")
    body = client.post(
        "/models/sam3.1-meta/predict/upload",
        params={"prompt": "wheat head"},
        files={"file": ("photo.jpg", photograph, "image/jpeg")},
    ).json()
    assert body["model"] == "sam3.1-meta"
    assert "cached" not in body, "an upload is not a fact about a published row"


@pytest.mark.skipif(
    os.environ.get("GLOBAL_WHEAT_LIVE_BIG_UPLOAD") != "1",
    reason="16 MB of bytes; set GLOBAL_WHEAT_LIVE_BIG_UPLOAD=1 to spend the link on the 413",
)
def test_an_upload_over_fifteen_megabytes_is_refused(client: httpx.Client) -> None:
    """The edge refuses the size the site publishes."""
    payload = b"\xff\xd8" + os.urandom(16 * 1024 * 1024)
    answer = client.post(
        "/models/sam3.1-meta/predict/upload",
        params={"prompt": "wheat head"},
        files={"file": ("big.jpg", payload, "image/jpeg")},
    )
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "payload_too_large"


# --------------------------------------------------------------------------- answers and resources


def test_a_repeated_question_is_answered_from_the_store(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """The same canonical request twice is one inference; a different one is another."""
    require(models, "sam3.1-meta")
    params = {"prompt": f"pre-release {uuid.uuid4()}", "imgsz": 672}
    first = client.post(
        "/models/sam3.1-meta/predict", params=params, json={"dataset": "gwhd", "row": 4}
    ).json()
    second = client.post(
        "/models/sam3.1-meta/predict", params=params, json={"dataset": "gwhd", "row": 4}
    ).json()
    third = client.post(
        "/models/sam3.1-meta/predict",
        params={**params, "imgsz": 784},
        json={"dataset": "gwhd", "row": 4},
    ).json()
    assert second.get("cached") is True
    assert first.get("cached") is not True
    assert third.get("cached") is not True


def test_metadata_takes_no_inference_lane(
    client: httpx.Client, models: dict[str, dict[str, Any]]
) -> None:
    """Three inferences in flight are the team's share; a listing still answers."""
    require(models, "wevisone-vl-4b")

    def ask(index: int) -> int:
        """One inference with a query nothing else asked."""
        return client.post(
            "/models/wevisone-vl-4b/predict",
            params={"prompt": f"wheat head {uuid.uuid4()}"},
            json={"dataset": "gwhd", "row": index},
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        running = [pool.submit(ask, row) for row in (1, 2, 3, 4)]
        metadata = [
            client.get("/datasets").status_code,
            client.get("/models").status_code,
            client.get("/datasets/gwhd/images/0").status_code,
        ]
        codes = [future.result() for future in running]

    assert codes.count(429) >= 1, f"four inferences were not refused: {codes}"
    assert 200 in codes
    assert metadata == [200, 200, 200]
