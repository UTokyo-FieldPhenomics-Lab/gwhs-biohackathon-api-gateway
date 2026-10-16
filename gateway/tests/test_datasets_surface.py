"""The dataset surface over HTTP: the four routes, their shapes, and their refusals."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import GENERATED_AT, REVISION
from fastapi.testclient import TestClient

from biohack_gateway.app import create_app
from biohack_gateway.tokens import TokenPool

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"
KEY = {"X-API-Key": TOKEN}
BASE = "https://data.example"


def _cursor(document: str) -> str:
    """Encode a cursor body the way the surface does, for a test that feeds one back."""
    return base64.urlsafe_b64encode(document.encode()).decode()


@pytest.fixture
def client(tmp_path: Path, index_dir: Path) -> Iterator[TestClient]:
    """Give a client answering from the fixture index, with errors instead of tracebacks."""
    pool = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(pool)
    app = create_app(tokens_path=pool, index_dir=index_dir, data_base_url=BASE)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_the_directory_lists_every_dataset_with_its_shape(client: TestClient) -> None:
    """The directory is one line per dataset: size, splits, and how its annotation is shaped."""
    response = client.get("/datasets", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "datasets": [
            {
                "id": "gwfss",
                "rows": 2,
                "splits": {"labelled": 2},
                "geometry": None,
                "mask": {"kind": "semantic"},
            },
            {
                "id": "gwhd",
                "rows": 2,
                "splits": {"train": 1, "validation": 1},
                "geometry": "boxes",
                "mask": None,
            },
            {
                "id": "gwhs-temp",
                "rows": 2,
                "splits": {"train": 2},
                "geometry": "rle",
                "mask": None,
            },
        ]
    }


def test_a_semantic_description_carries_the_palette_and_the_facets(client: TestClient) -> None:
    """The palette is fetched once per session here, and nowhere else in the surface."""
    response = client.get("/datasets/gwfss", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "dataset": "gwfss",
        "revision": REVISION,
        "rules": "0.1.0",
        "license": "cc-by-4.0",
        "rows": 2,
        "generated_at": GENERATED_AT,
        "splits": {"labelled": 2},
        "classes": [
            {"id": 0, "name": "background", "color": "#000000"},
            {"id": 1, "name": "head", "color": "#32ff84"},
            {"id": 2, "name": "stem", "color": "#3284ff"},
            {"id": 3, "name": "leaf", "color": "#d6ff32"},
        ],
        "filters": {"split": {"labelled": 2}, "class": {"head": 2, "stem": 1, "leaf": 2}},
    }


def test_an_object_description_has_no_palette(client: TestClient) -> None:
    """A dataset without a class vocabulary omits the key rather than answering an empty list."""
    body = client.get("/datasets/gwhs-temp", headers=KEY).json()

    assert "classes" not in body
    assert list(body["filters"]) == ["split", "tags", "category", "attribute"]


def test_a_list_page_carries_statistics_and_never_the_annotation(client: TestClient) -> None:
    """A list is identification, addresses, and counts; the geometry is one row at a time."""
    response = client.get("/datasets/gwhs-temp/images", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "dataset": "gwhs-temp",
        "count": 2,
        "next_cursor": None,
        "images": [
            {
                "row": 0,
                "split": "train",
                "width": 592,
                "height": 2509,
                "image_url": f"{BASE}/gwhs-temp/images/00000000.jpg",
                "mask_url": None,
                "image_id": "149_ear_001.JPG",
                "statistics": {"objects": 2, "tags": 2},
            },
            {
                "row": 1,
                "split": "train",
                "width": 600,
                "height": 300,
                "image_url": f"{BASE}/gwhs-temp/images/00000001.png",
                "mask_url": None,
                "image_id": "150_ear_002.png",
                "statistics": {"objects": 1, "tags": 1},
            },
        ],
    }


def test_a_semantic_list_page_reports_the_classes_present(client: TestClient) -> None:
    """A semantic row's statistics are the classes its mask has, not a count of objects."""
    body = client.get("/datasets/gwfss/images", headers=KEY).json()

    assert body["images"][0] == {
        "row": 0,
        "split": "labelled",
        "width": 512,
        "height": 512,
        "image_url": f"{BASE}/gwfss/images/00000000.png",
        "mask_url": f"{BASE}/gwfss/masks/00000000.png",
        "statistics": {"classes": ["head", "leaf"]},
    }
    assert body["images"][1]["statistics"] == {"classes": ["head", "stem", "leaf"]}


def test_a_detection_list_page_carries_the_row_metadata(client: TestClient) -> None:
    """A field a dataset carries is present, and a null value stays visible."""
    body = client.get("/datasets/gwhd/images", headers=KEY).json()

    assert body["images"][0] == {
        "row": 0,
        "split": "train",
        "width": 1024,
        "height": 1024,
        "image_url": f"{BASE}/gwhd/images/00000000.jpg",
        "mask_url": None,
        "domain": "field",
        "country": "Germany",
        "location": "Einbeck",
        "development_stage": "tillering",
        "statistics": {"objects": 2},
    }
    assert body["images"][1]["location"] is None
    assert "image_id" not in body["images"][1]


@pytest.mark.parametrize(
    ("query", "rows"),
    [
        ("?tags=Labo", [0, 1]),
        ("?tags=healthy", [0]),
        ("?category=spikelet_-_side2", [0]),
        ("?attribute=Green-Spikelet", [0]),
        ("?split=train", [0, 1]),
        ("?tags=Labo&tags=healthy", [0]),
    ],
)
def test_a_filter_narrows_the_page(client: TestClient, query: str, rows: list[int]) -> None:
    """Every filter key the description offers selects the rows it claims to."""
    body = client.get(f"/datasets/gwhs-temp/images{query}", headers=KEY).json()

    assert [image["row"] for image in body["images"]] == rows
    assert body["count"] == len(rows)


def test_a_filter_key_the_dataset_does_not_have_is_an_empty_page(client: TestClient) -> None:
    """A dataset that cannot answer a key returns nothing, rather than refusing the request."""
    response = client.get("/datasets/gwfss/images?country=Germany", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {"dataset": "gwfss", "count": 0, "next_cursor": None, "images": []}


def test_limit_and_cursor_walk_the_page(client: TestClient) -> None:
    """The cursor is opaque and resumes exactly where the page it came with ended."""
    first = client.get("/datasets/gwhs-temp/images?limit=1", headers=KEY).json()

    assert first["count"] == 1
    assert [image["row"] for image in first["images"]] == [0]
    cursor = first["next_cursor"]
    assert cursor is not None
    assert base64.urlsafe_b64decode(cursor) == b'{"o":1}'

    second = client.get(f"/datasets/gwhs-temp/images?limit=1&cursor={cursor}", headers=KEY).json()

    assert [image["row"] for image in second["images"]] == [1]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("limit", ["0", "1001", "-1", "many"])
def test_a_limit_outside_the_range_is_a_bad_request(client: TestClient, limit: str) -> None:
    """The page has a ceiling and a floor, and either violation is the contract's 400."""
    response = client.get(f"/datasets/gwhd/images?limit={limit}", headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "cursor", ["not-base64!", _cursor('{"o":-1}'), _cursor('{"n":1}'), _cursor("[1]")]
)
def test_a_cursor_that_is_not_one_is_a_bad_request(client: TestClient, cursor: str) -> None:
    """A cursor is ours or it is nothing, and neither is guessed at."""
    response = client.get("/datasets/gwhd/images", params={"cursor": cursor}, headers=KEY)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_the_detail_returns_the_annotation_with_its_geometry(client: TestClient) -> None:
    """One row at a time: the objects, their boxes, and the RLE exactly as the upstream wrote it."""
    response = client.get("/datasets/gwhs-temp/images/0", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "dataset": "gwhs-temp",
        "row": 0,
        "split": "train",
        "width": 592,
        "height": 2509,
        "image_url": f"{BASE}/gwhs-temp/images/00000000.jpg",
        "mask_url": None,
        "image_id": "149_ear_001.JPG",
        "tags": ["Labo", "healthy"],
        "objects": [
            {
                "idx": 0,
                "category": "spikelet_-_side1",
                "bbox": [231.0, 923.0, 126.0, 162.0],
                "area": 12074.0,
                "attributes": ["Green-Spikelet"],
                "rle": {"size": [2509, 592], "counts": "abc"},
            },
            {
                "idx": 1,
                "category": "spikelet_-_side2",
                "bbox": [208.0, 939.0, 41.0, 77.0],
                "area": 1222.0,
                "attributes": ["Green-Spikelet"],
                "rle": {"size": [2509, 592], "counts": "def"},
            },
        ],
    }


def test_the_detail_omits_what_the_dataset_does_not_carry(client: TestClient) -> None:
    """A box has no area and a semantic row has no objects, so neither key is invented."""
    detection = client.get("/datasets/gwhd/images/0", headers=KEY).json()
    semantic = client.get("/datasets/gwfss/images/0", headers=KEY).json()

    assert detection["objects"] == [
        {"idx": 0, "category": "0", "bbox": [936.0, 4.0, 41.0, 78.0]},
        {"idx": 1, "category": "0", "bbox": [10.0, 20.0, 30.0, 40.0]},
    ]
    assert "tags" not in detection
    assert "image_id" not in detection
    assert "objects" not in semantic
    assert semantic["mask_url"] == f"{BASE}/gwfss/masks/00000000.png"


def test_an_unknown_dataset_or_row_is_not_found(client: TestClient) -> None:
    """A miss is a 404 whose message repeats nothing the caller sent."""
    for path in ("/datasets/nope", "/datasets/nope/images", "/datasets/gwhd/images/99"):
        response = client.get(path, headers=KEY)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    assert "nope" not in client.get("/datasets/nope", headers=KEY).text


@pytest.mark.parametrize(
    "path",
    ["/datasets", "/datasets/gwhd", "/datasets/gwhd/images", "/datasets/gwhd/images/0"],
)
def test_every_dataset_route_needs_a_key(client: TestClient, path: str) -> None:
    """The surface inherits the gate by adding a route, and nothing here is anonymous."""
    assert client.get(path).status_code == 401


def test_the_routes_are_part_of_the_published_contract(client: TestClient) -> None:
    """The generated reference is a projection of these models, so the routes must be in it."""
    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {
        "/health",
        "/datasets",
        "/datasets/{dataset}",
        "/datasets/{dataset}/images",
        "/datasets/{dataset}/images/{row}",
        "/models",
        "/models/{model}",
        "/models/{model}/predict",
        "/models/{model}/predict/upload",
    }
