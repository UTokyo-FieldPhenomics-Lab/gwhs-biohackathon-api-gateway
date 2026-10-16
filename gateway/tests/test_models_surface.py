"""The model surface over HTTP: the directory, one model, and predicts that reach a backend.

The backend is a fake — a function answering for a pool of machines — because what is under test
here is what the gateway does *around* a backend: resolving an address through the index, forwarding
parameters it does not interpret, passing over a machine that just failed, and reporting whose fault
a failure was. What a model actually computes is the container's business and is tested there.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from biohack_gateway.app import create_app
from biohack_gateway.models import ModelRegistry, Reply
from biohack_gateway.tokens import TokenPool

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"
KEY = {"X-API-Key": TOKEN}
BASE = "https://data.example"

MODEL = "sam3.1-meta"
FIRST = "http://192.0.2.10:8000"
SECOND = "http://192.0.2.11:8000"
BACKEND_TOKEN = "backend-secret"

WEIGHTS = ["prompt", "confidence_threshold"]
"""The two parameters the fixture model declares, in the order it declares them."""

DECLARATION: dict[str, Any] = {
    "model": MODEL,
    "resolution": 1008,
    "max_in_flight": 1,
    "parameters": [
        {"name": "prompt", "default": "wheat head", "description": "What to look for."},
        {
            "name": "confidence_threshold",
            "default": 0.5,
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "How sure before an object is reported.",
        },
    ],
}

PREDICTION: dict[str, Any] = {
    "model": MODEL,
    "resolution_used": 1008,
    "objects": [
        {
            "idx": 0,
            "category": "wheat head",
            "bbox": [2321.0, 1876.7, 766.1, 766.7],
            "area": 259818.0,
            "score": 0.582,
            "rle": {"size": [3456, 5184], "counts": "e[Ue"},
        }
    ],
}
"""One answer in the shape the two surfaces share, including the `score` only a model produces."""


class FakeBackends:
    """Answer for a pool of backends, and remember every request made to them.

    Attributes
    ----------
    asked : list of tuple
        One ``(method, url, headers, body)`` per request, in order, so a test can assert what was
        forwarded rather than only what came back.
    """

    def __init__(
        self,
        replies: Mapping[str, list[Reply]] | None = None,
        declaration: dict[str, Any] | None = None,
        health: list[Reply] | None = None,
    ) -> None:
        """Program the answers.

        Parameters
        ----------
        replies : mapping of str to list of Reply, optional
            Per-backend queues, consumed in order; the last is repeated. A backend with no queue
            answers a successful prediction.
        declaration : dict, optional
            What a backend answers when asked what it serves. `None` answers the fixture's.
        health : list of Reply, optional
            A queue for the declaration reads alone, consumed across all backends and repeated at
            the end. It is how a test models a container that is still loading when the gateway
            starts and ready a moment later.
        """
        self.replies = {url: list(queue) for url, queue in (replies or {}).items()}
        self.declaration = DECLARATION if declaration is None else declaration
        self.health = list(health or [])
        self.asked: list[tuple[str, str, Mapping[str, str], bytes | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Reply:
        """Answer one request, the way a real backend would."""
        self.asked.append((method, url, dict(headers), body))
        if url.endswith("/health"):
            if self.health:
                return self.health.pop(0) if len(self.health) > 1 else self.health[0]
            if self.declaration is None:
                return Reply(status=0, body=b"")
            return Reply(status=200, body=json.dumps(self.declaration).encode())
        for base, queue in self.replies.items():
            if url.startswith(base):
                if len(queue) > 1:
                    return queue.pop(0)
                return queue[0]
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    def predict_calls(self) -> list[tuple[str, str, Mapping[str, str], bytes | None]]:
        """Return only the predict requests, which is what most tests care about.

        Returns
        -------
        list of tuple
            The same tuples :attr:`asked` holds, minus the startup declarations.
        """
        return [call for call in self.asked if "/health" not in call[1]]


@pytest.fixture
def backends() -> FakeBackends:
    """Give a pool of two backends that both answer a prediction."""
    return FakeBackends()


def build_client(
    tmp_path: Path,
    index_dir: Path,
    backends: FakeBackends,
    models: Any = None,
    backend_token: str = BACKEND_TOKEN,
) -> Iterator[TestClient]:
    """Build a client whose model surface answers through the fake backends.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory.
    index_dir : Path
        The fixture index the addresses resolve through.
    backends : FakeBackends
        The transport standing in for the machines.
    models : Any
        The model-to-backend list to write. `None` writes the fixture's two backends.
    backend_token : str
        The token the gateway presents to backends.

    Returns
    -------
    Iterator of TestClient
        The client, as a context manager.
    """
    pool = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(pool)
    models_path = tmp_path / "models.json"
    document = {MODEL: [FIRST, SECOND]} if models is None else models
    models_path.write_text(
        document if isinstance(document, str) else json.dumps(document), encoding="utf-8"
    )
    app = create_app(
        tokens_path=pool,
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=models_path,
        backend_token=backend_token,
        model_transport=backends,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def client(tmp_path: Path, index_dir: Path, backends: FakeBackends) -> Iterator[TestClient]:
    """Give a client over the fixture index and the fake backends."""
    yield from build_client(tmp_path, index_dir, backends)


def test_the_directory_lists_what_the_model_declares(client: TestClient) -> None:
    """The directory is one line per model, carrying the parameters it accepts."""
    response = client.get("/models", headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "models": [
            {
                "id": MODEL,
                "resolution": 1008,
                "backends_up": 2,
                "max_in_flight": 2,
                "parameters": DECLARATION["parameters"],
            }
        ]
    }


def test_one_model_is_the_entry_the_directory_carries(client: TestClient) -> None:
    """The detail route answers the same object, so an id can be quoted."""
    directory = client.get("/models", headers=KEY).json()["models"]
    detail = client.get(f"/models/{MODEL}", headers=KEY)

    assert detail.status_code == 200
    assert detail.json() == directory[0]


def test_a_model_nobody_configured_is_refused(client: TestClient) -> None:
    """An unconfigured id is a 404 in the contract's own shape."""
    response = client.get("/models/sam9.9", headers=KEY)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_predict_resolves_the_address_and_returns_the_models_answer(
    client: TestClient, backends: FakeBackends
) -> None:
    """The gateway resolves `(dataset, row)` and hands the backend a public URL, not a path."""
    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert response.json() == PREDICTION
    method, url, headers, body = backends.predict_calls()[0]
    assert method == "POST"
    assert url == f"{FIRST}/models/{MODEL}/predict"
    assert headers["X-Backend-Token"] == BACKEND_TOKEN
    assert json.loads(body or b"{}") == {"input": {"url": f"{BASE}/gwhd/images/00000000.jpg"}}


def test_the_parameters_are_forwarded_verbatim(client: TestClient, backends: FakeBackends) -> None:
    """The gateway does not know the parameter names, so it passes the query string through."""
    response = client.post(
        f"/models/{MODEL}/predict?prompt=tassle&confidence_threshold=0.7",
        headers=KEY,
        json={"dataset": "gwhd", "row": 1},
    )

    assert response.status_code == 200
    assert backends.predict_calls()[0][1].endswith(
        f"/models/{MODEL}/predict?prompt=tassle&confidence_threshold=0.7"
    )


def test_a_client_supplied_url_is_not_a_way_in(client: TestClient) -> None:
    """A body may name an address, never one to fetch — that is the dataset surface's rule."""
    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"url": "http://192.0.2.99/secret.jpg"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_a_row_that_does_not_exist_is_refused(client: TestClient) -> None:
    """An address that does not resolve never reaches a backend."""
    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 99}
    )

    assert response.status_code == 404


def test_a_failed_backend_is_passed_over_and_the_next_one_answers(
    tmp_path: Path, index_dir: Path
) -> None:
    """A machine that just failed is not asked again for thirty seconds."""
    backends = FakeBackends(replies={FIRST: [Reply(status=500, body=b"boom")]})
    client = next(build_client(tmp_path, index_dir, backends))

    first = client.post(f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0})
    second = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    tried = [url for _, url, _, _ in backends.predict_calls()]
    assert tried == [
        f"{FIRST}/models/{MODEL}/predict",
        f"{SECOND}/models/{MODEL}/predict",
        f"{SECOND}/models/{MODEL}/predict",
    ]


def test_a_backend_fault_is_reported_as_an_upstream_fault(tmp_path: Path, index_dir: Path) -> None:
    """When every machine failed, the caller is told the upstream failed and may try elsewhere."""
    backends = FakeBackends(
        replies={
            FIRST: [Reply(status=500, body=b"boom")],
            SECOND: [Reply(status=500, body=b"boom")],
        }
    )
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_a_slow_backend_is_reported_as_a_timeout(tmp_path: Path, index_dir: Path) -> None:
    """A clock that ran out is its own code, because a caller retries it differently."""
    backends = FakeBackends(
        replies={FIRST: [Reply(status=0, body=b"", timed_out=True)]},
        declaration=None,
    )
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200  # the second backend answered
    backends.replies = {}
    for url in (FIRST, SECOND):
        backends.replies[url] = [Reply(status=0, body=b"", timed_out=True)]
    timed_out = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "upstream_timeout"


def test_the_callers_mistake_is_not_retried_on_another_machine(
    tmp_path: Path, index_dir: Path, backends: FakeBackends
) -> None:
    """A 4xx from a backend is the request's fault, so an identical request would fail again."""
    backends.replies = {FIRST: [Reply(status=400, body=b'{"error": {"code": "invalid_request"}}')]}
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 400
    assert len(backends.predict_calls()) == 1


def test_an_upload_is_forwarded_as_bytes(client: TestClient, backends: FakeBackends) -> None:
    """The one path that carries bytes forwards them and does not store them."""
    payload = b"\xff\xd8\xff\xe0 not really a jpeg, but bytes"
    response = client.post(
        f"/models/{MODEL}/predict/upload?prompt=head",
        headers=KEY,
        files={"file": ("field.jpg", payload, "image/jpeg")},
    )

    assert response.status_code == 200
    _, url, headers, body = backends.predict_calls()[0]
    assert url == f"{FIRST}/models/{MODEL}/predict/upload?prompt=head"
    assert headers["Content-Type"] == "image/jpeg"
    assert body == payload


def test_an_empty_upload_is_refused(client: TestClient) -> None:
    """Nothing to segment is the request's fault, and is said before a backend is asked."""
    response = client.post(
        f"/models/{MODEL}/predict/upload",
        headers=KEY,
        files={"file": ("empty.jpg", b"", "image/jpeg")},
    )

    assert response.status_code == 400


def test_a_backend_that_answers_outside_the_contract_is_an_upstream_fault(
    tmp_path: Path, index_dir: Path, backends: FakeBackends
) -> None:
    """A body that is not this contract is a broken machine, not a broken request."""
    backends.replies = {FIRST: [Reply(status=200, body=b"not json")]}
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 502


def test_a_gateway_without_a_backend_token_fails_closed(tmp_path: Path, index_dir: Path) -> None:
    """A misconfigured gateway is unavailable, not something the participant did wrong."""
    backends = FakeBackends()
    client = next(build_client(tmp_path, index_dir, backends, backend_token=""))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 503
    assert backends.predict_calls() == []


def test_a_models_file_that_does_not_parse_means_no_models(tmp_path: Path, index_dir: Path) -> None:
    """Host configuration that is wrong yields an empty directory, never an open one."""
    backends = FakeBackends()
    client = next(build_client(tmp_path, index_dir, backends, models="{not json"))

    assert client.get("/models", headers=KEY).json() == {"models": []}
    assert client.get(f"/models/{MODEL}", headers=KEY).status_code == 404


def test_a_backend_that_is_switched_off_is_listed_with_nothing_declared(
    tmp_path: Path, index_dir: Path
) -> None:
    """A machine that was off at startup still routes; it just has not said what it serves."""
    backends = FakeBackends(declaration={})  # answers, but says nothing about itself
    client = next(build_client(tmp_path, index_dir, backends))

    entry = client.get("/models", headers=KEY).json()["models"][0]

    assert entry["resolution"] is None
    assert entry["parameters"] == []
    assert entry["backends_up"] == 2


def test_a_container_still_loading_describes_itself_once_it_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway and a container start independently, so a refusal must not be permanent.

    This is the ordinary boot race: the gateway is up first, the container is still loading its
    weights and answers ``503``, and nobody is going to restart the gateway afterwards. The registry
    asks again, which is what makes the second describe the whole truth.
    """
    monkeypatch.setattr("biohack_gateway.models.DECLARATION_LOADING_RETRY_SECONDS", 0.0)
    loading = Reply(status=503, body=b'{"ready": false}')
    ready = Reply(status=200, body=json.dumps(DECLARATION).encode())
    backends = FakeBackends(health=[loading, ready])
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)

    described = registry.describe(MODEL)

    assert described.resolution == 1008
    assert [parameter.name for parameter in described.parameters] == WEIGHTS


def test_a_backend_that_declared_nothing_is_not_asked_on_every_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry is bounded: a machine that is off is asked once per window, not once per call."""
    monkeypatch.setattr("biohack_gateway.models.DECLARATION_RETRY_SECONDS", 3600.0)
    backends = FakeBackends(health=[Reply(status=0, body=b"")])
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)

    registry.describe(MODEL)
    registry.describe(MODEL)
    registry.directory()

    assert len(backends.asked) == 1
