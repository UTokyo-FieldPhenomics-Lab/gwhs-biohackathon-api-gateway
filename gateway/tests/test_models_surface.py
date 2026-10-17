"""The model surface over HTTP: the directory, one model, and predicts that reach a backend.

The backend is a fake — a function answering for a pool of machines — because what is under test
here is what the gateway does *around* a backend: resolving an address through the index, forwarding
parameters it does not interpret, passing over a machine that just failed, and reporting whose fault
a failure was. What a model actually computes is the container's business and is tested there.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from biohack_gateway.app import create_app
from biohack_gateway.models import (
    RESPONSE_TIMEOUT_SECONDS,
    UNHEALTHY_SECONDS,
    ModelRegistry,
    Reply,
    http_transport,
    watch_pool,
)
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
    "computed_by": {
        "machine": "lab-3090",
        "gpu": "NVIDIA GeForce RTX 3090",
        "compute_capability": "8.6",
        "cuda": "12.8",
        "torch": "2.10.0+cu128",
    },
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
    "computed_by": {
        "machine": "lab-3090",
        "gpu": "NVIDIA GeForce RTX 3090",
        "compute_capability": "8.6",
        "cuda": "12.8",
        "torch": "2.10.0+cu128",
    },
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
    """The directory is one line per model, carrying its parameters and the machines behind it."""
    response = client.get("/models", headers=KEY)

    assert response.status_code == 200
    body = response.json()
    # The age of the last look is part of the count, and it is the one number a test cannot predict
    # to the second, so it is checked as a value rather than compared as one.
    age = body["models"][0].pop("checked_seconds_ago")
    assert isinstance(age, int) and 0 <= age < 60
    assert body == {
        "models": [
            {
                "id": MODEL,
                "resolution": 1008,
                "backends_up": 2,
                "max_in_flight": 2,
                "parameters": DECLARATION["parameters"],
                # Two backends, two records — one per machine that answered, not one per model. Two
                # identical entries mean an operator named two machines the same, which is theirs to
                # fix and not the gateway's to hide.
                "machines": [DECLARATION["computed_by"], DECLARATION["computed_by"]],
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


def test_an_answer_without_a_record_of_its_machine_still_passes(
    tmp_path: Path, index_dir: Path, backends: FakeBackends
) -> None:
    """A backend that has not been rebuilt answers the old shape, and that is not an error.

    The record is optional for exactly one reason: rolling out a new field must not turn every
    running backend into a failure case. The answer passes through, and the field is simply absent.
    """
    backends.replies = {
        FIRST: [
            Reply(
                status=200,
                body=json.dumps(
                    {k: v for k, v in PREDICTION.items() if k != "computed_by"}
                ).encode(),
            )
        ]
    }
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert "computed_by" not in response.json()


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


def test_a_machine_that_never_answered_is_not_published_as_up(
    tmp_path: Path, index_dir: Path
) -> None:
    """A machine that was off when the process started is not "up" for want of a failure.

    This was the count's oldest lie: `backends_up` counted the backends that had not *recently
    failed*, and a machine that had never answered at all had failed against nothing — so a pair of
    switched-off machines was published as two backends up, and a reader was told to press Run.
    """
    backends = FakeBackends(health=[Reply(status=0, body=b"")])
    client = next(build_client(tmp_path, index_dir, backends))

    entry = client.get("/models", headers=KEY).json()["models"][0]

    assert entry["backends_up"] == 0
    assert entry["max_in_flight"] == 0
    assert entry["checked_seconds_ago"] == 0  # it was asked; it said nothing
    refused = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )
    assert refused.status_code == 503  # the published zero means the same thing to a caller


def test_a_probe_finds_a_machine_that_dies_and_one_that_comes_back() -> None:
    """Availability is asked, not inferred: no request has to fail for the count to change.

    The same pass must not re-read what the model serves. A declaration is a specification, and one
    that changes under a running process is a deployment that has not been restarted — so the probe
    asks *whether*, never *what*, and a resolution of 700 arriving on port 8000 changes nothing.
    """
    ready = Reply(status=200, body=json.dumps(DECLARATION).encode())
    changed = Reply(status=200, body=json.dumps({**DECLARATION, "resolution": 700}).encode())
    silence = Reply(status=0, body=b"")
    backends = FakeBackends(health=[ready, changed, silence, changed])
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)

    assert registry.describe(MODEL).backends_up == 1  # the startup declaration is the first look

    registry.probe()
    described = registry.describe(MODEL)
    assert described.backends_up == 1
    assert described.resolution == 1008  # the declaration was not read a second time

    registry.probe()
    assert registry.describe(MODEL).backends_up == 0  # found without a request failing
    assert 0 <= (registry.describe(MODEL).checked_seconds_ago or 0) < 60  # and it says how new

    registry.probe()
    assert registry.describe(MODEL).backends_up == 1  # and back, with nothing restarted


def test_the_watch_loop_keeps_asking_until_it_is_cancelled() -> None:
    """The loop is the clock: every machine, every interval, and no further once cancelled."""
    backends = FakeBackends()
    registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)
    asked_at_start = len(backends.asked)

    async def run() -> None:
        watching = asyncio.create_task(watch_pool(registry, interval=0.01))
        await asyncio.sleep(0.05)
        watching.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watching

    asyncio.run(run())

    # More than one pass, and both machines in each: the two are not ordered in the assertion, only
    # counted, because the probe itself decides the order.
    assert len(backends.asked) - asked_at_start >= 4


def test_the_application_starts_the_probe_with_the_process(
    tmp_path: Path, index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop belongs to the app: entering it starts the clock, leaving it stops the clock.

    Without this, the loop is a function nobody calls, and every published age is the startup one.
    """
    monkeypatch.setattr("biohack_gateway.models.PROBE_INTERVAL_SECONDS", 0.01)
    backends = FakeBackends()
    pool = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(pool)
    models_path = tmp_path / "models.json"
    models_path.write_text(json.dumps({MODEL: [FIRST]}), encoding="utf-8")
    app = create_app(
        tokens_path=pool,
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=models_path,
        backend_token=BACKEND_TOKEN,
        model_transport=backends,
    )

    with TestClient(app, raise_server_exceptions=False):
        before = len(backends.asked)
        time.sleep(0.1)
        driven = len(backends.asked) - before

    assert driven >= 1, "the app started without asking the pool again"

    stopped = len(backends.asked)
    time.sleep(0.05)
    # One last pass may already have been in its worker thread when the task was cancelled; a second
    # one would mean the loop outlived the app.
    assert len(backends.asked) - stopped <= 1


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


def test_the_failure_memory_outlives_the_failure_it_remembers() -> None:
    """A backend that just cost a request a full budget is not asked again before that is paid.

    The arithmetic that `deploy/gpu-backends.md` measured the hard way: a fallback took 61.2 s
    because the gateway waited a whole 60-second request timeout and then passed the backend
    over for only 30 — shorter than the wait it had just caused, so the request after the window
    paid it again.
    """
    assert UNHEALTHY_SECONDS >= RESPONSE_TIMEOUT_SECONDS


def test_a_burst_is_served_by_the_idle_backend_while_the_first_one_hangs(
    tmp_path: Path, index_dir: Path
) -> None:
    """The request that arrives while another is stuck does not wait for the stuck one.

    A backend is only *known* to be unhealthy after an attempt fails, so without the in-flight
    preference every request that arrives during the failure waits the failure out too.
    """
    hanging = threading.Event()
    released = threading.Event()

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        if url.startswith(FIRST):
            hanging.set()
            released.wait(5)
            return Reply(status=0, body=b"", timed_out=True)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    client = next(build_client(tmp_path, index_dir, transport))  # type: ignore[arg-type]
    ask = {"dataset": "gwhd", "row": 0}

    first = threading.Thread(
        target=lambda: client.post(f"/models/{MODEL}/predict", headers=KEY, json=ask)
    )
    first.start()
    assert hanging.wait(5), "the first request never reached the first backend"

    started = time.monotonic()
    second = client.post(f"/models/{MODEL}/predict", headers=KEY, json=ask)
    elapsed = time.monotonic() - started

    released.set()
    first.join(5)

    assert second.status_code == 200
    assert elapsed < 2.0, "the second request queued behind the hanging backend"


def test_the_transport_really_speaks_http_to_a_listening_backend() -> None:
    """The one place the code that talks to a real backend is exercised.

    Every other test injects a transport, so nothing else covers `http_transport`: `http.client`
    rather than `urllib`, because the connect phase needs its own budget, and a redirect is not
    followed — see the function's notes.
    """
    seen: list[tuple[str, Mapping[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        """A backend that records what arrived and answers a body."""

        def do_POST(self) -> None:
            """Record the request line and headers, and answer as a backend does."""
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append((self.path, dict(self.headers)))
            body = b'{"model": "sam3.1-meta", "objects": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            """Keep the request log off the test's stderr."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_address[1]
        reply = http_transport(
            "POST",
            f"http://127.0.0.1:{port}/models/{MODEL}/predict?prompt=wheat%20head",
            {"X-Backend-Token": BACKEND_TOKEN, "Content-Type": "application/json"},
            b'{"input": {"url": "https://data.example/x.jpg"}}',
            5.0,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert reply.status == 200
    assert json.loads(reply.body)["model"] == MODEL
    path, headers = seen[0]
    assert path == f"/models/{MODEL}/predict?prompt=wheat%20head", "the query is forwarded verbatim"
    assert headers["X-Backend-Token"] == BACKEND_TOKEN


def test_a_backend_that_accepts_and_never_answers_times_out_at_the_budget() -> None:
    """Reproduce the shape a stopped container behind Docker Desktop's proxy has.

    The socket opens — the port is still bound by the proxy — and then nothing answers. Nothing but
    the clock can end this one, which is why the response budget is the whole of what a participant
    waits for a dead backend.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    budget = 0.4
    try:
        started = time.monotonic()
        reply = http_transport("POST", f"http://127.0.0.1:{port}/x", {}, b"", budget)
        elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert reply.status == 0
    assert reply.timed_out is True
    assert budget <= elapsed < 5.0, "the response budget, not the OS default"


def test_a_refused_connection_is_not_reported_as_a_timeout() -> None:
    """A port with nothing behind it is a different fact, and the two read differently in a log."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    reply = http_transport("POST", f"http://127.0.0.1:{port}/x", {}, b"", 5.0)

    assert reply.status == 0
    assert reply.timed_out is False


def test_choosing_a_backend_and_reserving_it_are_one_step() -> None:
    """Two requests choosing at the same instant do not both take the same backend.

    Reaches for `_begin` deliberately: the property *is* that choosing and counting happen in one
    critical section, and no arrangement of two public calls can show it without racing. Measured on
    2026-10-16: with the two steps apart, two of three simultaneous requests still queued behind a
    stopped backend.
    """
    registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, FakeBackends())

    chosen = [registry._begin(MODEL)[0].url for _ in range(2)]  # noqa: SLF001 — see the docstring

    assert chosen == [FIRST, SECOND]


def test_a_burst_of_requests_is_spread_over_both_backends(tmp_path: Path, index_dir: Path) -> None:
    """Every request in a burst gets an answer, and only the first meets the failed backend.

    One authenticated request goes first, deliberately. The token pool reads its file on the first
    request that needs it and marks itself checked *before* the pool it read is in place, so a burst
    arriving inside that window is refused by the request gate — a real race in `auth.py`, unrelated
    to routing, that eight simultaneous requests could not reproduce against the live gateway.
    `GET /models` is used to warm it because it authenticates and touches no backend.
    """
    hanging = threading.Event()
    released = threading.Event()
    met: list[str] = []

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        met.append(url)
        if url.startswith(FIRST):
            hanging.set()
            released.wait(5)
            return Reply(status=0, body=b"", timed_out=True)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    client = next(build_client(tmp_path, index_dir, transport))  # type: ignore[arg-type]
    assert client.get("/models", headers=KEY).status_code == 200, "warm the token pool first"

    ask = {"dataset": "gwhd", "row": 0}
    answers: list[int] = []

    def send() -> None:
        answers.append(client.post(f"/models/{MODEL}/predict", headers=KEY, json=ask).status_code)

    burst = [threading.Thread(target=send) for _ in range(4)]
    for thread in burst:
        thread.start()
    assert hanging.wait(5), "no request ever reached the hanging backend"
    released.set()
    for thread in burst:
        thread.join(10)

    assert answers == [200, 200, 200, 200]
    assert sum(1 for url in met if url.startswith(FIRST)) == 1, (
        "the burst queued behind the failure"
    )


def test_the_least_busy_backend_is_chosen_when_every_one_is_busy() -> None:
    """A burst deeper than the pool goes to the shortest queue, not to the head of the list.

    Emptying the idle group leaves the busy group in the order the host listed it, and a request
    arriving then took `busy[0]` whatever that backend already held. Measured against the live
    gateway on 2026-10-17 with three cards and 160 simultaneous calls: 140 went to the first entry
    and the other two cards saw 20 between them, so the pool answered at one card's rate.
    """
    registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, FakeBackends())
    registry._begin(MODEL)  # noqa: SLF001 — FIRST now holds one request
    registry._begin(MODEL)  # noqa: SLF001 — SECOND now holds one
    registry._reserve(registry._backends[MODEL][0])  # noqa: SLF001 — FIRST holds two, SECOND one

    chosen = registry._begin(MODEL)[0].url  # noqa: SLF001

    assert chosen == SECOND, "the request joined the longer queue"


def _served_by_uvicorn(app: Any) -> Iterator[int]:
    """Run the application on a real listening socket, the way the host does.

    Concurrency is the property under test, and `TestClient` answers one request per call inside a
    portal of its own, which cannot show an event loop held by a handler. A real server can.

    Parameters
    ----------
    app : Any
        The application to serve.

    Yields
    ------
    int
        The port it listens on.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(500):
        if server.started:
            break
        time.sleep(0.01)
    yield port
    server.should_exit = True
    thread.join(5)


def test_an_upload_does_not_hold_the_loop_while_a_backend_works(
    tmp_path: Path, index_dir: Path
) -> None:
    """An upload is one thread, not the whole gateway: other routes keep answering meanwhile.

    The route read its body with `await` and then called the blocking transport inside the event
    loop, so every other request waited for the backend round trip. Measured against the live
    gateway on 2026-10-17 with six concurrent uploads: `/health` answered in 11.08 s and 5.31 s,
    and in 1.2 ms the moment the uploads drained.
    """
    delay = 0.3

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        time.sleep(delay)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    app = create_app(
        tokens_path=_pool_path(tmp_path),
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=_models_path(tmp_path),
        backend_token=BACKEND_TOKEN,
        model_transport=transport,
    )
    served = _served_by_uvicorn(app)
    port = next(served)
    base = f"http://127.0.0.1:{port}"
    try:
        assert httpx.get(f"{base}/models", headers=KEY, timeout=10).status_code == 200
        gate = threading.Barrier(5)
        started = time.monotonic()

        def upload() -> None:
            gate.wait()
            httpx.post(
                f"{base}/models/{MODEL}/predict/upload",
                headers=KEY,
                files={"file": ("own.jpg", b"\xff\xd8 not really a jpeg", "image/jpeg")},
                timeout=30,
            )

        senders = [threading.Thread(target=upload) for _ in range(4)]
        for sender in senders:
            sender.start()
        gate.wait()
        time.sleep(0.05)
        asked = time.monotonic()
        health = httpx.get(f"{base}/health", timeout=30)
        held = time.monotonic() - asked
        for sender in senders:
            sender.join(30)
        whole = time.monotonic() - started

        assert health.status_code == 200
        assert held < delay / 2, f"/health waited {held:.2f} s behind the uploads"
        assert whole < 4 * delay * 0.75, f"four uploads took {whole:.2f} s, one after another"
    finally:
        next(served, None)


def _pool_path(tmp_path: Path) -> Path:
    """Write the single-team token pool the served app authenticates against."""
    pool = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(pool)
    return pool


def _models_path(tmp_path: Path) -> Path:
    """Write a model list naming the fixture's two backends."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({MODEL: [FIRST, SECOND]}), encoding="utf-8")
    return path
