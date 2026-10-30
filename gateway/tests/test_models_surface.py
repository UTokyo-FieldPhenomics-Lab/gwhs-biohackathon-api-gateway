"""The model surface over HTTP: the directory, one model, and predicts that reach a backend.

The backend is a fake — a function answering for a pool of machines — because what is under test
here is what the gateway does *around* a backend: resolving an address through the index, forwarding
parameters it does not interpret, passing over a machine that just failed, and reporting whose fault
a failure was. What a model actually computes is the container's business and is tested there.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
import uvicorn
from fastapi import HTTPException
from fastapi.testclient import TestClient

from biohack_gateway.app import create_app, occupies_lane
from biohack_gateway.cache import ResultCache
from biohack_gateway.models import (
    RESPONSE_TIMEOUT_SECONDS,
    UNHEALTHY_SECONDS,
    ComputedBy,
    ModelRegistry,
    Reply,
    _BoundedHTTPConnection,
    _checkin,
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
THIRD = "http://192.0.2.12:8000"
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


def _named_after_its_address(declaration: dict[str, Any], url: str) -> dict[str, Any]:
    """Copy the fixture's declaration with the backend named after its own address.

    Parameters
    ----------
    declaration : dict
        The declaration the fake was programmed with.
    url : str
        The `/health` address the backend was asked on.

    Returns
    -------
    dict
        A copy whose ``computed_by.machine`` is that address's host — the fixture's way of saying
        "two addresses are two machines". A test that wants several containers on one card passes a
        declaration with a fixed machine name instead.

    Notes
    -----
    The name matters to the router now: requests are counted per machine, so a fixture whose
    backends all claim one card would have them share a queue, which is the opposite of what the
    ordering tests are about.
    """
    record = declaration.get("computed_by")
    if not isinstance(record, dict):
        return declaration
    return {**declaration, "computed_by": {**record, "machine": httpx.URL(url).host}}


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
            What a backend answers when asked what it serves. `None` answers the fixture's, with
            each backend named as its own machine after its address — a pool of separate machines,
            which is what most tests mean. Pass one whose ``computed_by.machine`` is fixed to model
            several containers sharing one card.
        health : list of Reply, optional
            A queue for the declaration reads alone, consumed across all backends and repeated at
            the end. It is how a test models a container that is still loading when the gateway
            starts and ready a moment later.
        """
        self.replies = {url: list(queue) for url, queue in (replies or {}).items()}
        self.declaration = DECLARATION if declaration is None else declaration
        self.names_machines = declaration is None
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
            declaration = self.declaration
            if self.names_machines:
                declaration = _named_after_its_address(declaration, url)
            return Reply(status=200, body=json.dumps(declaration).encode())
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


class _FrozenClock:
    """A stand-in for the `time` module whose clock does not advance.

    Attributes
    ----------
    now : float
        The stamp every call answers with.
    """

    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        """Return the frozen stamp."""
        return self.now


@contextmanager
def _no_round_trip() -> Iterator[None]:
    """Build a registry, or the app holding one, with every backend the same distance away.

    The fake transport answers in microseconds, so almost all of what `_ask` times is the fake's own
    execution, and the first call in the process is measurably slower than the last. Read as a round
    trip, that noise makes the last-listed backend the nearest one, and every test naming `FIRST`
    would be asserting the clock rather than the routing. A frozen clock makes each measurement
    zero — the "not yet measured" seed — so the scores are equal and the host's list order decides,
    which is the order these tests were written against. A test that is about distance assigns
    `Backend.rtt_seconds` itself.
    """
    with mock.patch("biohack_gateway.models.time", _FrozenClock(time.monotonic())):
        yield


def build_client(
    tmp_path: Path,
    index_dir: Path,
    backends: FakeBackends,
    models: Any = None,
    backend_token: str = BACKEND_TOKEN,
    max_concurrent_per_token: int | None = None,
    cache: ResultCache | None = None,
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
    max_concurrent_per_token : int or None
        The per-token cap to build with. `None` follows the pool's width, and `0` disables it,
        which is what a test of routing alone wants.
    cache : ResultCache or None, optional
        The store the route reads. `None` builds the disabled one the suite runs with, so a test
        that needs a stored answer passes its own file.

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
    with _no_round_trip():
        app = create_app(
            tokens_path=pool,
            index_dir=index_dir,
            data_base_url=BASE,
            models_path=models_path,
            backend_token=backend_token,
            model_transport=backends,
            max_concurrent_per_token=max_concurrent_per_token,
            cache=cache,
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
                "declared": True,
                "parameters": DECLARATION["parameters"],
                # Two backends, two records — one per machine that answered, not one per model. The
                # fixture names each address as its own machine, which is what the pool is: two
                # cards answering, and a reader told which.
                "machines": [
                    {**DECLARATION["computed_by"], "machine": "192.0.2.10"},
                    {**DECLARATION["computed_by"], "machine": "192.0.2.11"},
                ],
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


def test_a_stored_answer_the_contract_cannot_read_is_dropped(
    tmp_path: Path, index_dir: Path
) -> None:
    """A cache outlives a deploy, and a stale body must cost a miss rather than a permanent 502."""
    path = tmp_path / "cache.sqlite"
    store = ResultCache(path, enabled=True)
    backends = FakeBackends()
    # The generator owns the client's lifespan: dropping it here would shut the app down and
    # close the store before the first request.
    stream = build_client(tmp_path, index_dir, backends, cache=store)
    client = next(stream)

    first = client.post(f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0})
    assert first.status_code == 200
    assert len(backends.predict_calls()) == 1

    # What an older contract wrote: a body this one will not read as a model answer.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE answers SET body = ?", (b'{"nonsense": true}',))
        connection.commit()

    second = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(backends.predict_calls()) == 2, "a body outside the contract was served instead"
    assert store.stats().entries == 1, "the unreadable entry was left in place"
    stream.close()


def test_a_geometry_only_answer_may_carry_no_category(tmp_path: Path, index_dir: Path) -> None:
    """A point with no text prompt has no label, and the contract says so instead of refusing it.

    The model publishes `category: null` for an answer it could not name; the object shape used to
    require a string, which turned a documented combination of parameters into a `502`.
    """
    answer = dict(PREDICTION)
    answer["objects"] = [{**PREDICTION["objects"][0], "category": None}]
    backends = FakeBackends(replies={FIRST: [Reply(status=200, body=json.dumps(answer).encode())]})
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert response.json()["objects"][0]["category"] is None


COUNTING: dict[str, Any] = {
    "model": MODEL,
    "resolution_used": 384,
    "count": 42.0,
    "density": {"size": [3, 3], "values": [0.0, 6.0, 0.0, 0.0, 12.0, 0.0, 0.0, 24.0, 0.0]},
    "objects": [],
}
"""A counting model's answer: a number, the field it was summed from, and the empty object list D3
keeps required. The cells add up to the count, which is what makes the field readable rather than
decorative."""

POINTED: dict[str, Any] = {
    "model": MODEL,
    "resolution_used": 384,
    "count": 2.0,
    "points": [[16.0, 32.0, 1.0], [48.0, 64.0, 0.25]],
    "objects": [],
}
"""A point-counting model's answer: points instead of a field. No container here emits it; the field
stays in the contract because deleting it would mean rebuilding the shape and the page the day one
does."""


def test_a_counting_answer_keeps_its_count_and_field(tmp_path: Path, index_dir: Path) -> None:
    """A count and the field it integrates cross the gateway rather than being dropped.

    The container declares nothing special for them: this is the same body shape a mask model
    answers, with `objects` empty and two fields carrying what a counter found instead.
    """
    backends = FakeBackends(
        replies={FIRST: [Reply(status=200, body=json.dumps(COUNTING).encode())]}
    )
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert response.json() == COUNTING
    cells = response.json()["density"]["values"]
    assert round(sum(cells), 6) == response.json()["count"]


def test_a_point_answer_keeps_its_points(tmp_path: Path, index_dir: Path) -> None:
    """The point shape is relayed unchanged for the model family that answers it."""
    backends = FakeBackends(replies={FIRST: [Reply(status=200, body=json.dumps(POINTED).encode())]})
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert response.json() == POINTED
    assert "density" not in response.json()


def test_a_mask_answer_gains_no_count_density_or_points(tmp_path: Path, index_dir: Path) -> None:
    """The fields are optional: an answer that has none is the one it was before they existed."""
    backends = FakeBackends(
        replies={FIRST: [Reply(status=200, body=json.dumps(PREDICTION).encode())]}
    )
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert response.json() == PREDICTION
    assert "count" not in response.json()
    assert "density" not in response.json()
    assert "points" not in response.json()


def test_a_field_the_contract_does_not_name_is_still_dropped(
    tmp_path: Path, index_dir: Path
) -> None:
    """A new field is not a licence for every field: the whole shape is still this contract."""
    answer = {**PREDICTION, "heatmap": [[0.0, 1.0], [2.0, 3.0]]}
    backends = FakeBackends(replies={FIRST: [Reply(status=200, body=json.dumps(answer).encode())]})
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert response.json() == PREDICTION


def test_a_density_that_is_not_its_shape_is_refused(tmp_path: Path, index_dir: Path) -> None:
    """A named field is checked: `density` is `size` and `values`, so a bare grid is refused."""
    answer = {**COUNTING, "density": [[0.0, 1.0], [2.0, 3.0]]}
    backends = FakeBackends(replies={FIRST: [Reply(status=200, body=json.dumps(answer).encode())]})
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 502


def test_a_counting_answer_without_objects_is_still_outside_the_contract(
    tmp_path: Path, index_dir: Path
) -> None:
    """`objects` stays required: a counter answers `[]`, and a body omitting it is a broken one."""
    answer = {key: value for key, value in COUNTING.items() if key != "objects"}
    backends = FakeBackends(replies={FIRST: [Reply(status=200, body=json.dumps(answer).encode())]})
    client = next(build_client(tmp_path, index_dir, backends))

    response = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 502


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
    """A machine that answered as a proxy that cannot reach its model is not asked again."""
    backends = FakeBackends(replies={FIRST: [Reply(status=502, body=b"boom")]})
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


def test_one_server_error_is_answered_without_passing_the_machine_over() -> None:
    """A `500` is one request the backend could not handle, not a machine that is gone.

    Passing the backend over on the first one let a single crashing request take a model off the
    pool for thirty seconds — and retrying that same body on the next machine is how the crash
    spread. The caller is told, and the machine keeps its place.
    """
    backends = FakeBackends(replies={FIRST: [Reply(status=500, body=b"boom")]})
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    with pytest.raises(HTTPException) as refused:
        registry.forward(MODEL, "predict", "", b"{}", "application/json")

    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the counter
    assert refused.value.status_code == 502
    assert backend.unhealthy_until == 0.0
    assert [url for _, url, _, _ in backends.predict_calls()] == [f"{FIRST}/models/{MODEL}/predict"]


def test_three_server_errors_in_a_row_pass_the_machine_over() -> None:
    """A container that answers `500` to everything is a machine that is gone, eventually.

    The probe cannot see this: an application that fails still answers `/health` with `200`. A short
    run of `5xx` is the line between one bad request and a backend that should be passed over.
    """
    backends = FakeBackends(replies={FIRST: [Reply(status=500, body=b"boom")]})
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    outcomes: list[int] = []
    for _ in range(3):
        try:
            outcomes.append(
                registry.forward(MODEL, "predict", "", b"{}", "application/json").status
            )
        except HTTPException as refused:
            outcomes.append(refused.status_code)

    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the counter
    assert outcomes == [502, 502, 200], "the third answer should have come from the second machine"
    assert backend.unhealthy_until > 0.0


def test_a_network_failure_breaks_the_run_of_server_errors() -> None:
    """The `5xx` counter is a run *in a row*, so a timeout between two of them clears it.

    A machine whose application is failing answers `500` every time; a lost packet in the middle of
    that says nothing about the application and must not make the run look longer than it was. The
    counter is what decides when three bad answers become "this container is broken".
    """
    backends = FakeBackends(replies={FIRST: [Reply(status=500, body=b"boom")]})
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the counter
    for _ in range(2):
        with pytest.raises(HTTPException):
            registry.forward(MODEL, "predict", "", b"{}", "application/json")
    assert backend.server_errors == 2

    backends.replies[FIRST] = [Reply(status=0, body=b"", timed_out=True, stage="read")]
    registry.forward(MODEL, "predict", "", b"{}", "application/json")
    assert backend.server_errors == 0, "a timeout was counted as an application failure"

    backends.replies[FIRST] = [Reply(status=500, body=b"boom")]
    with pytest.raises(HTTPException):
        registry.forward(MODEL, "predict", "", b"{}", "application/json")
    assert backend.server_errors == 1
    assert backend.unhealthy_until == 0.0, "the run was not restarted after the timeout"


def test_a_send_that_times_out_is_not_a_backend_failure() -> None:
    """The upload's link belongs to this host, so the machine keeps its place and gets no re-send.

    A fifteen-megabyte body on a degraded path ended on the clock before the backend saw a request.
    Treating that as the backend's fault shut a healthy machine out of the pool and spent the same
    send again on the next one.
    """
    backends = FakeBackends(
        replies={FIRST: [Reply(status=0, body=b"", timed_out=True, stage="send")]}
    )
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    for _ in range(2):
        with pytest.raises(HTTPException) as refused:
            registry.forward(MODEL, "predict/upload", "", b"\x89PNG", "image/png")
        assert refused.value.status_code == 504

    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the counter
    assert backend.unhealthy_until == 0.0
    assert [url for _, url, _, _ in backends.predict_calls()] == [
        f"{FIRST}/models/{MODEL}/predict/upload",
        f"{FIRST}/models/{MODEL}/predict/upload",
    ], "the body was re-sent to another machine"


def test_one_lost_attempt_is_not_a_verdict_on_the_machine() -> None:
    """A connect that ran out of clock is as likely to be a dropped packet as a machine that is off.

    Measured on 2026-10-29 from the laboratory LAN: about 2% of TCP segments were being
    retransmitted to every off-campus destination, and one 2 kB JSON call took 9.07 s. A single
    timeout is therefore evidence about the path, and charging it to the card took a healthy machine
    out of the pool for thirty seconds while the link was already slow. The second decides it.
    """
    backends = FakeBackends(
        replies={FIRST: [Reply(status=0, body=b"", timed_out=True, stage="connect")]}
    )
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    answered = registry.forward(MODEL, "predict", "", b"{}", "application/json")
    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the pool's state
    assert answered.status == 200, "the fallback was not asked"
    assert backend.unhealthy_until == 0.0, "one lost attempt was charged to the machine"
    assert backend.last_failure == "timeout", "the pool cannot say what failed"

    registry.forward(MODEL, "predict", "", b"{}", "application/json")
    assert backend.unhealthy_until > 0.0, "two in a row did not take the machine out of the pool"


def test_a_refusal_is_a_verdict_on_the_machine_at_once() -> None:
    """Nothing listening is proof about the machine, not about the path: it answered and refused.

    The distinction is the whole point of naming the failure: a refusal arrives in one round trip
    with a reset in it, so a lost packet cannot produce one, and waiting for a second costs a
    request's worth of clock for nothing.
    """
    backends = FakeBackends(replies={FIRST: [Reply(status=0, body=b"", failure="refused")]})
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    registry.forward(MODEL, "predict", "", b"{}", "application/json")

    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the pool's state
    assert backend.unhealthy_until > 0.0
    assert backend.last_failure == "refused"


def test_a_probe_that_lost_a_packet_does_not_retire_the_machine() -> None:
    """The probe is the other witness, and one silent one is not a verdict either.

    It is the only evidence about a machine nobody is calling, so its verdict is what `backends_up`
    publishes. Reading one unanswered probe as a dead machine made the count fall exactly when the
    campus egress was dropping packets — the moment a reader most needs it to be true.
    """
    ready = Reply(status=200, body=json.dumps(DECLARATION).encode())
    lost = Reply(status=0, body=b"", timed_out=True, stage="connect")
    backends = FakeBackends(health=[ready, lost, lost, ready])
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)

    assert registry.describe(MODEL).backends_up == 1  # the startup declaration is the first look

    registry.probe()
    assert registry.describe(MODEL).backends_up == 1, "one lost probe retired a machine"
    assert registry.machines()[0].failures == 1, "the failed probe was not recorded"

    registry.probe()
    assert registry.describe(MODEL).backends_up == 0, "two lost probes did not"

    registry.probe()
    assert registry.describe(MODEL).backends_up == 1  # and back, with nothing restarted


def test_the_pool_says_which_failure_and_how_old(tmp_path: Path, index_dir: Path) -> None:
    """An operator reading `/pool` must be able to tell a dead machine from a lossy link.

    `alive` alone cannot: it is false for both. The kind, its stage and its age can — a machine that
    is alive with a connect timeout a second ago is a path problem, and one that refused ten seconds
    ago with no answer since is a container that is not running.
    """
    backends = FakeBackends(
        replies={FIRST: [Reply(status=0, body=b"", timed_out=True, stage="connect")]}
    )
    client = next(build_client(tmp_path, index_dir, backends))

    answered = client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert answered.status_code == 200
    entry = next(
        machine
        for machine in client.get("/pool", headers=KEY).json()["machines"]
        if machine["machine"] == httpx.URL(FIRST).host
    )
    assert entry["last_failure"] == "timeout at connect"
    assert entry["failures"] == 1
    assert entry["failed_seconds_ago"] == 0
    assert entry["alive"] is True, "the probe never said it was down"
    assert entry["passed_over"] is False, "one loss was enough to stop routing to it"


def test_a_backend_refusal_is_rewritten_into_this_contract() -> None:
    """A backend's status and words are its own; a participant reads one contract.

    `422` would break the promise that a bad request is `400`; a `404` from a backend means the
    gateway is misconfigured, which is `502` rather than the caller's mistake; and only a complaint
    about the parameters is worth passing on, because anything else may name an address inside the
    pool.
    """
    backends = FakeBackends(
        replies={
            FIRST: [
                Reply(status=422, body=b'{"detail": "prompt is too long"}'),
                Reply(status=404, body=b'{"detail": "http://192.0.2.10:8000/internal"}'),
            ]
        }
    )
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)

    with pytest.raises(HTTPException) as unprocessable:
        registry.forward(MODEL, "predict", "prompt=x", b"{}", "application/json")
    with pytest.raises(HTTPException) as missing:
        registry.forward(MODEL, "predict", "", b"{}", "application/json")

    assert unprocessable.value.status_code == 400
    assert "prompt is too long" in unprocessable.value.detail
    assert missing.value.status_code == 502
    assert "192.0.2.10" not in missing.value.detail


def test_a_gateway_without_a_token_leaves_no_lane_counted() -> None:
    """The refusal is decided before a machine is reserved, so no count is stranded."""
    backends = FakeBackends()
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST]}, "", backends)

    with pytest.raises(HTTPException) as refused:
        registry.forward(MODEL, "predict", "", b"{}", "application/json")

    backend = registry._backends[MODEL][0]  # noqa: SLF001 — the assertion is the counter
    assert refused.value.status_code == 503
    assert backend.in_flight == 0
    assert registry._machine_in_flight == {}  # noqa: SLF001 — same counter, per machine


def _inference_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the per-attempt inference lines a test captured, in emission order.

    Parameters
    ----------
    caplog : pytest.LogCaptureFixture
        The capturing fixture, already raised to `INFO` on the models logger.

    Returns
    -------
    list of str
        The records' formatted messages, with the startup and probe lines left out.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "biohack_gateway.models" and record.getMessage().startswith("inference ")
    ]


def _fields(line: str) -> dict[str, str]:
    """Parse one `key=value` log line into a mapping, for assertions on named fields."""
    return dict(item.split("=", 1) for item in line.split()[1:])


def test_an_attempt_is_logged_with_its_backend_and_size(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One line per attempt, carrying counts and addresses but never the request's content."""
    backends = FakeBackends()
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)
    body = b'{"input": {"url": "https://data.example/gwhd/images/00000000.jpg"}}'

    with caplog.at_level(logging.INFO, logger="biohack_gateway.models"):
        reply = registry.forward(MODEL, "predict", "prompt=wheat", body, "application/json")

    assert reply.status == 200
    lines = _inference_lines(caplog)
    assert len(lines) == 1
    fields = _fields(lines[0])
    assert fields["model"] == MODEL
    assert fields["backend"] == FIRST
    assert fields["mode"] == "predict"
    assert fields["attempt"] == "0"
    assert fields["ahead"] == "0"
    assert fields["machine_ahead"] == "0"
    assert fields["status"] == "200"
    assert float(fields["ms"]) >= 0.0
    assert fields["bytes_in"] == str(len(body))
    assert fields["bytes_out"] == str(len(reply.body))
    assert "prompt=wheat" not in lines[0], "the query is the participant's content"


def test_a_fallback_attempt_says_which_backend_it_reached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second machine tried in the same request logs `attempt=1` against that machine."""
    backends = FakeBackends(replies={FIRST: [Reply(status=502, body=b"boom")]})
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, backends)

    with caplog.at_level(logging.INFO, logger="biohack_gateway.models"):
        reply = registry.forward(MODEL, "predict/upload", "", b"\x89PNG\r\n\x1a\n", "image/png")

    assert reply.status == 200
    fields = [_fields(line) for line in _inference_lines(caplog)]
    assert [(entry["attempt"], entry["backend"], entry["status"]) for entry in fields] == [
        ("0", FIRST, "502"),
        ("1", SECOND, "200"),
    ]
    assert [entry["mode"] for entry in fields] == ["predict/upload", "predict/upload"]


def test_an_attempt_counts_the_requests_already_ahead_of_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`ahead` is the queue depth this attempt joined, read in the same step that counted it."""
    started = threading.Event()
    released = threading.Event()
    predictions = 0

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        """Block the first inference until the test says so; answer the rest at once."""
        nonlocal predictions
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        predictions += 1
        if predictions == 1:
            started.set()
            released.wait(5)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, transport)  # type: ignore[arg-type]
    body = b"{}"
    answered: list[Reply] = []
    with caplog.at_level(logging.INFO, logger="biohack_gateway.models"):
        first = threading.Thread(
            target=lambda: answered.append(
                registry.forward(MODEL, "predict", "", body, "application/json")
            )
        )
        first.start()
        assert started.wait(5), "the first request never reached the backend"
        second = registry.forward(MODEL, "predict", "", body, "application/json")
        released.set()
        first.join(5)

    assert second.status == 200
    assert len(answered) == 1
    depths = [(entry["ahead"], entry["status"]) for entry in map(_fields, _inference_lines(caplog))]
    assert ("1", "200") in depths, "the second attempt did not say it queued behind the first"
    assert ("0", "200") in depths, "the first attempt did not report an empty queue"


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
    weights and answers ``503``, and nobody is going to restart the gateway afterwards. The probe
    asks again — on its own clock, not a request's — which is what makes the next describe the whole
    truth.
    """
    monkeypatch.setattr("biohack_gateway.models.DECLARATION_LOADING_RETRY_SECONDS", 0.0)
    loading = Reply(status=503, body=b'{"ready": false}')
    ready = Reply(status=200, body=json.dumps(DECLARATION).encode())
    backends = FakeBackends(health=[loading, ready])
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)

    registry.probe()

    described = registry.describe(MODEL)

    assert described.resolution == 1008
    assert [parameter.name for parameter in described.parameters] == WEIGHTS


def test_describing_a_model_asks_no_backend() -> None:
    """A page load must not wait on a machine that is switched off.

    The retry for an undeclared backend belongs to the probe. Asking from `describe` made a request
    for any model with one dead backend wait the declaration budget, once per retry window — which
    is the clock `gateway.md` says only the probe owns.
    """
    backends = FakeBackends(health=[Reply(status=0, body=b"")])
    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, backends)
    asked_at_startup = len(backends.asked)

    registry.describe(MODEL)
    registry.describe(MODEL)
    registry.directory()

    assert asked_at_startup == 1, "the startup declaration is the one call this test expects"
    assert len(backends.asked) == asked_at_startup


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


def test_a_reused_connection_takes_the_budget_of_the_call_that_took_it() -> None:
    """The pool's most likely hand-me-down is the probe's connection, and the probe is in a hurry.

    ``HTTPConnection.timeout`` is read when a socket is *created*, not afterwards, so a kept
    connection carries the budget it was made with — and the probe makes its own with
    :data:`DECLARATION_TIMEOUT_SECONDS`. A predict that took one from the pool without setting the
    socket's timeout failed any inference longer than three seconds, on a backend that was fine.
    """

    class Slow(BaseHTTPRequestHandler):
        """A backend that answers slowly enough to outlast the probe's budget."""

        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            """Answer the probe at once, so its connection is what the pool keeps."""
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            """Read the body, think for longer than the probe's budget, and answer."""
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            time.sleep(0.35)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            """Keep the request log off the test's stderr."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        probe = http.client.HTTPConnection("127.0.0.1", port, timeout=0.05)
        probe.request("GET", "/health")
        probe.getresponse().read()
        _checkin(("http", "127.0.0.1", port), probe)

        reply = http_transport(
            "POST", f"http://127.0.0.1:{port}/models/{MODEL}/predict", {}, b"{}", 5.0
        )
    finally:
        server.shutdown()
        server.server_close()

    assert reply.status == 200
    assert reply.timed_out is False


def test_a_send_the_clock_ended_is_marked_as_the_send() -> None:
    """The phase decides what the failure means: the link, or the machine.

    A body large enough to fill the socket buffers against a peer that never reads is the shape of a
    degraded path. Without the phase, `forward` read that as a backend that had failed and shut a
    healthy machine out of the pool.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with mock.patch("biohack_gateway.models.MIN_SEND_BYTES_PER_SECOND", 100_000_000.0):
            reply = http_transport("POST", f"http://127.0.0.1:{port}/x", {}, b"x" * 8_000_000, 0.2)
    finally:
        listener.close()

    assert reply.status == 0
    assert reply.timed_out is True
    assert reply.stage == "send"


def test_choosing_a_backend_and_reserving_it_are_one_step() -> None:
    """Two requests choosing at the same instant do not both take the same backend.

    Reaches for `_begin` deliberately: the property *is* that choosing and counting happen in one
    critical section, and no arrangement of two public calls can show it without racing. Measured on
    2026-10-16: with the two steps apart, two of three simultaneous requests still queued behind a
    stopped backend.
    """
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, FakeBackends())

    chosen = [
        registry._begin(MODEL, "predict")[0][0].url
        for _ in range(2)  # noqa: SLF001 — see the docstring
    ]

    assert chosen == [FIRST, SECOND]


def test_a_burst_of_requests_is_spread_over_both_backends(tmp_path: Path, index_dir: Path) -> None:
    """Every request in a burst gets an answer, and a lost backend meets at most one more after it.

    One authenticated request goes first, deliberately. The token pool reads its file on the first
    request that needs it and marks itself checked *before* the pool it read is in place, so a burst
    arriving inside that window is refused by the request gate — a real race in `auth.py`, unrelated
    to routing, that eight simultaneous requests could not reproduce against the live gateway.
    `GET /models` is used to warm it because it authenticates and touches no backend.

    The failed backend is a timeout, so it is retired by the *second* request that meets it rather
    than the first: one unanswered attempt is also what a dropped packet looks like, and the burst
    still answers every request through the fallback. The request sent after the burst is what
    proves the retirement happened — it must not meet the failed machine at all.
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

    # The cap is off so this measures routing and nothing else: a burst of four from one key is
    # refused after two by the pool's width, which is a different test's property.
    client = next(
        build_client(tmp_path, index_dir, transport, max_concurrent_per_token=0)  # type: ignore[arg-type]
    )
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

    after = client.post(f"/models/{MODEL}/predict", headers=KEY, json=ask)

    assert answers == [200, 200, 200, 200]
    assert sum(1 for url in met if url.startswith(FIRST)) <= 2, (
        "more than one request met the backend after its second unanswered attempt"
    )
    assert after.status_code == 200
    assert met[-1].startswith(SECOND), "the machine was not retired after two unanswered attempts"


def test_the_least_busy_backend_is_chosen_when_every_one_is_busy() -> None:
    """A burst deeper than the pool goes to the shortest queue, not to the head of the list.

    Emptying the idle group leaves the busy group in the order the host listed it, and a request
    arriving then took `busy[0]` whatever that backend already held. Measured against the live
    gateway on 2026-10-17 with three cards and 160 simultaneous calls: 140 went to the first entry
    and the other two cards saw 20 between them, so the pool answered at one card's rate.
    """
    with _no_round_trip():
        registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, FakeBackends())
    registry._begin(MODEL, "predict")  # noqa: SLF001 — FIRST now holds one request
    registry._begin(MODEL, "predict")  # noqa: SLF001 — SECOND now holds one
    registry._reserve(registry._backends[MODEL][0])  # noqa: SLF001 — FIRST holds two, SECOND one

    chosen = registry._begin(MODEL, "predict")[0][0].url  # noqa: SLF001

    assert chosen == SECOND, "the request joined the longer queue"


def _registry_with_round_trips(*seconds: float) -> ModelRegistry:
    """Build a registry over the fixture's backends, each with a known round trip.

    Parameters
    ----------
    seconds : float
        One round trip per backend, in the order the host listed them — :data:`FIRST`, then
        :data:`SECOND`, then :data:`THIRD`.

    Returns
    -------
    ModelRegistry
        The registry. The figures are assigned rather than probed, so a test costs no sleep; the
        ordering never writes them, so they stay what the test said.

    Notes
    -----
    A test that probes afterwards would overwrite these with what the fake transport takes, which is
    why the tests below reach for `_order` and `_begin` instead.
    """
    urls = [FIRST, SECOND, THIRD][: len(seconds)]
    registry = ModelRegistry({MODEL: urls}, BACKEND_TOKEN, FakeBackends())
    for backend, rtt in zip(registry._backends[MODEL], seconds, strict=True):  # noqa: SLF001
        backend.rtt_seconds = rtt
    return registry


def test_a_nearer_backend_is_chosen_while_both_are_idle() -> None:
    """With nothing in flight the score is all round trip, and it outranks the host's list order."""
    registry = _registry_with_round_trips(0.26, 0.005)

    chosen = registry._begin(MODEL, "predict")[0][0].url  # noqa: SLF001

    assert chosen == SECOND, "the request took the first-listed machine over the nearer one"


def test_one_request_in_flight_on_the_nearer_backend_does_not_send_the_next_one_away() -> None:
    """A queue of one costs a quarter of a service time, which 5 ms against 260 ms does not buy.

    0.005 + 0.25 < 0.26, so the next request waits behind one rather than crossing the ocean to an
    idle machine. This is the change's regression guard: ordering by "idle first" sent it away,
    because it priced one in-flight request as a whole service time.
    """
    registry = _registry_with_round_trips(0.005, 0.26)
    registry._begin(MODEL, "predict")  # noqa: SLF001 — FIRST now holds one request

    chosen = registry._begin(MODEL, "predict")[0][0].url  # noqa: SLF001

    assert chosen == FIRST


def test_two_requests_in_flight_on_the_nearer_backend_send_the_next_one_to_the_far_one() -> None:
    """0.005 + 0.5 > 0.26: the second queued request costs more than the ocean does."""
    registry = _registry_with_round_trips(0.005, 0.26)
    near = registry._backends[MODEL][0]  # noqa: SLF001
    registry._reserve(near)  # noqa: SLF001 — FIRST now holds two requests
    registry._reserve(near)  # noqa: SLF001

    chosen = registry._begin(MODEL, "predict")[0][0].url  # noqa: SLF001

    assert chosen == SECOND


def test_an_upload_pays_the_upload_queue_cost_and_a_row_does_not() -> None:
    """The threshold moves with the mode: one queued upload is already more than the ocean.

    One request in flight gives the near backend 0.005 + 0.25 = 0.255 in row mode — still nearer
    than 0.26 — and 0.005 + 0.5 = 0.505 in upload mode. The same queue, two answers, because an
    upload's service time is the longer one.
    """
    registry = _registry_with_round_trips(0.005, 0.26)
    registry._begin(MODEL, "predict/upload")  # noqa: SLF001 — FIRST now holds one upload

    order = registry._backends[MODEL]  # noqa: SLF001
    assert registry._order(order, "predict")[0].url == FIRST  # noqa: SLF001
    assert registry._order(order, "predict/upload")[0].url == SECOND  # noqa: SLF001


def test_a_burst_with_equal_round_trips_still_spreads_over_three_backends() -> None:
    """Equal scores keep list order, so a burst takes one backend each rather than the first's."""
    registry = _registry_with_round_trips(0.01, 0.01, 0.01)

    chosen = [registry._begin(MODEL, "predict")[0][0].url for _ in range(3)]  # noqa: SLF001

    assert chosen == [FIRST, SECOND, THIRD]


def _one_card(models: dict[str, list[str]]) -> ModelRegistry:
    """Build a registry in which every backend listed sits on one card named ``card-1``.

    Parameters
    ----------
    models : dict
        The model-to-backend list.

    Returns
    -------
    ModelRegistry
        The registry, with every backend declaring ``card-1`` and an equal round trip, so the only
        thing that can order them is the card's queue.

    Notes
    -----
    This is the shape a machine carrying several containers publishes: the same
    ``computed_by.machine`` from each, which is what makes their queues one queue.
    """
    registry = ModelRegistry(models, BACKEND_TOKEN, FakeBackends())
    for backends in registry._backends.values():  # noqa: SLF001
        for backend in backends:
            backend.machine = ComputedBy(machine="card-1")
            backend.rtt_seconds = 0.0
    return registry


def test_a_card_with_two_containers_prefers_the_one_whose_lock_is_free() -> None:
    """One card, two containers: the next request goes to the container doing nothing.

    Both score alike — the card is the queue — so the tie is broken by the container's own depth.
    Without that, list order would keep feeding the first container while the second sat idle.
    """
    registry = _one_card({MODEL: [FIRST, SECOND]})

    registry._begin(MODEL, "predict")  # noqa: SLF001 — FIRST holds one
    order = registry._order(registry._backends[MODEL], "predict")  # noqa: SLF001

    assert [backend.url for backend in order] == [SECOND, FIRST]


def test_two_models_on_one_card_share_one_queue() -> None:
    """A request for the second model is not told that the card is free.

    THIRD would be chosen either way: the point is that SECOND, on the busy card, loses. Without a
    per-machine count both cards look idle and the host's list order decides instead.
    """
    registry = _one_card({MODEL: [FIRST], "other": [SECOND, THIRD]})
    registry._backends["other"][1].machine = ComputedBy(machine="card-2")  # noqa: SLF001
    registry._begin(MODEL, "predict")  # noqa: SLF001 — one inference on card-1, through the other model

    order = registry._order(registry._backends["other"], "predict")  # noqa: SLF001

    assert [backend.url for backend in order] == [THIRD, SECOND]


def test_a_release_gives_the_card_its_slot_back() -> None:
    """The count falls when the attempt ends, and the key is dropped at zero."""
    registry = _one_card({MODEL: [FIRST, SECOND]})

    registry._begin(MODEL, "predict")  # noqa: SLF001
    assert registry._machine_in_flight == {"card-1": 1}  # noqa: SLF001

    registry._release(registry._backends[MODEL][0])  # noqa: SLF001

    assert registry._machine_in_flight == {}  # noqa: SLF001


def test_a_container_that_names_no_machine_is_counted_alone() -> None:
    """An undeclared machine falls back to the backend's own address, not to one shared bucket."""
    registry = ModelRegistry({MODEL: [FIRST, SECOND]}, BACKEND_TOKEN, FakeBackends())
    for backend in registry._backends[MODEL]:  # noqa: SLF001
        backend.machine = None
        backend.rtt_seconds = 0.0

    registry._begin(MODEL, "predict")  # noqa: SLF001

    assert registry._machine_in_flight == {FIRST: 1}  # noqa: SLF001


def test_a_probe_records_the_round_trip_and_a_503_leaves_it_alone() -> None:
    """Only an answer from a serving container measures the link.

    A `503` is a container still loading its weights: its duration says nothing about the route, so
    it must not move the figure the ordering is scored with.
    """
    replies = [
        Reply(status=200, body=json.dumps(DECLARATION).encode()),
        Reply(status=503, body=b'{"ready": false}'),
    ]

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        time.sleep(0.01)
        return replies.pop(0) if len(replies) > 1 else replies[0]

    registry = ModelRegistry({MODEL: [FIRST]}, BACKEND_TOKEN, transport)  # type: ignore[arg-type]
    backend = registry._backends[MODEL][0]  # noqa: SLF001
    measured = backend.rtt_seconds

    assert measured >= 0.005, "the first probe did not become the round trip"
    registry.probe()
    assert backend.rtt_seconds == measured, "a refusal rewrote the measurement"


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


def _models_path(tmp_path: Path, backends: list[str] | None = None) -> Path:
    """Write a model list naming the fixture's backends, two of them unless told otherwise.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory.
    backends : list of str, optional
        The backend addresses. Defaults to :data:`FIRST` and :data:`SECOND`.

    Returns
    -------
    Path
        The file to hand `create_app`.
    """
    path = tmp_path / "models.json"
    path.write_text(json.dumps({MODEL: backends or [FIRST, SECOND]}), encoding="utf-8")
    return path


def test_a_metadata_read_never_takes_an_inference_lane(tmp_path: Path, index_dir: Path) -> None:
    """A cap of one refuses the second *inference* and lets the directory answer anyway.

    A lane is GPU time; a dataset listing is an in-memory lookup that queues behind nothing. Before
    this was explicit, the middleware counted every request, so a page's own load — the dataset
    listing, a row's detail, the model directory — could be refused while no machine was busy.
    """
    delay = 0.6

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
        max_concurrent_per_token=1,
    )
    served = _served_by_uvicorn(app)
    port = next(served)
    base = f"http://127.0.0.1:{port}"
    ask = {"dataset": "gwhd", "row": 0}
    held: list[int] = []
    try:
        busy = threading.Thread(
            target=lambda: held.append(
                httpx.post(
                    f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30
                ).status_code
            )
        )
        busy.start()
        time.sleep(0.15)  # the inference is inside the backend now, holding the only lane

        while_inference = {
            "models": httpx.get(f"{base}/models", headers=KEY, timeout=30).status_code,
            "datasets": httpx.get(f"{base}/datasets", headers=KEY, timeout=30).status_code,
            "row": httpx.get(f"{base}/datasets/gwhd/images/0", headers=KEY, timeout=30).status_code,
            "second inference": httpx.post(
                f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30
            ).status_code,
        }
        busy.join(30)

        assert while_inference == {
            "models": 200,
            "datasets": 200,
            "row": 200,
            "second inference": 429,
        }, "the lane is for inference; the directory does not queue behind it"
        assert held == [200]
    finally:
        next(served, None)


def test_the_middleware_charges_only_the_upload_route() -> None:
    """The middleware's classifier, which is the whole of its rule, in one place.

    A row predict is not here: it looks the answer up in the cache first and takes a lane in the
    handler only on a miss, so the middleware must not charge it on the way in.
    """
    assert occupies_lane("/models/sam3.1-meta/predict/upload")
    for path in (
        "/models/sam3.1-meta/predict",
        "/models",
        "/models/sam3.1-meta",
        "/datasets",
        "/datasets/gwhd/images/0",
        "/health",
        "/play",
    ):
        assert not occupies_lane(path), path


def test_a_token_cannot_hold_more_than_three_requests_at_once(
    tmp_path: Path, index_dir: Path
) -> None:
    """A key may hold three inferences, and another team's key is untouched by it.

    The cap is what stops one team's loop from occupying every lane while others wait. Three is the
    policy number, not the pool's width: a team reaches the ceiling and never exceeds it. The fourth
    request is refused at once rather than queued, because a wait nobody can see is worse than an
    answer the client can act on.
    """
    delay = 0.5
    other_team, other_token = "gw-02", "Qv7mNp2Rt5Wx8Zc3Bk6Hd9Lj4Fs1Gy0At"
    other_key = {"X-API-Key": other_token}

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        time.sleep(delay)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    pool = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN, other_team: other_token}).save(pool)
    app = create_app(
        tokens_path=pool,
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=_models_path(tmp_path, [FIRST, SECOND, THIRD]),
        backend_token=BACKEND_TOKEN,
        model_transport=transport,
    )
    served = _served_by_uvicorn(app)
    port = next(served)
    base = f"http://127.0.0.1:{port}"
    ask = {"dataset": "gwhd", "row": 0}
    try:
        gate = threading.Barrier(4)
        held: list[int] = []

        def send() -> None:
            gate.wait()
            held.append(
                httpx.post(
                    f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30
                ).status_code
            )

        senders = [threading.Thread(target=send) for _ in range(3)]
        for sender in senders:
            sender.start()
        gate.wait()
        time.sleep(0.1)

        refused = httpx.post(f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30)
        stranger = httpx.post(
            f"{base}/models/{MODEL}/predict", headers=other_key, json=ask, timeout=30
        )
        for sender in senders:
            sender.join(30)

        assert held == [200, 200, 200], "the three in flight were served"
        assert refused.status_code == 429
        assert refused.json()["error"]["code"] == "quota_exceeded"
        assert stranger.status_code == 200, "the cap is per token, not per host"
        freed = httpx.post(f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30)
        assert freed.status_code == 200, "the cap was never released"
    finally:
        next(served, None)


def test_the_cap_does_not_widen_when_a_backend_declares_more(
    tmp_path: Path, index_dir: Path
) -> None:
    """Four declared lanes do not buy one key a fourth inference.

    The published width counts containers; the cap is a fairness policy, so a machine that gains
    another model must not widen every team's allowance with it. This is the whole point of the
    decoupling, and the two numbers are asserted to disagree.
    """
    delay = 0.4
    declaration = {**DECLARATION, "max_in_flight": 2}

    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(declaration).encode())
        time.sleep(delay)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    app = create_app(
        tokens_path=_pool_path(tmp_path),
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=_models_path(tmp_path, [FIRST, SECOND]),
        backend_token=BACKEND_TOKEN,
        model_transport=transport,
    )
    served = _served_by_uvicorn(app)
    port = next(served)
    base = f"http://127.0.0.1:{port}"
    ask = {"dataset": "gwhd", "row": 0}
    try:
        published = httpx.get(f"{base}/models", headers=KEY, timeout=30).json()
        ladder = threading.Barrier(4)
        held: list[int] = []

        def send() -> None:
            ladder.wait()
            held.append(
                httpx.post(
                    f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30
                ).status_code
            )

        senders = [threading.Thread(target=send) for _ in range(3)]
        for sender in senders:
            sender.start()
        ladder.wait()
        time.sleep(0.1)
        refused = httpx.post(f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30)
        for sender in senders:
            sender.join(30)

        assert published["models"][0]["max_in_flight"] == 4, "the pool did not publish its width"
        assert held == [200, 200, 200]
        assert refused.status_code == 429, "the cap followed the declared width"
    finally:
        next(served, None)


def test_the_cap_can_be_raised_for_one_measurement(tmp_path: Path, index_dir: Path) -> None:
    """A host may override the ceiling, which is how one key carries a load test.

    The alternative was a second class of credential that exists only for us and is the thing most
    likely to be forgotten before an event; an environment variable read once at startup leaves no
    standing bypass, because the policy default is back after the restart.
    """
    _assert_admitted(tmp_path, index_dir, cap=5, concurrent=6, served=5)


def test_the_cap_can_be_disabled_entirely(tmp_path: Path, index_dir: Path) -> None:
    """`0` is a measurement of the pool rather than a wider cap: nothing is refused."""
    _assert_admitted(tmp_path, index_dir, cap=0, concurrent=6, served=6)


def _assert_admitted(
    tmp_path: Path, index_dir: Path, cap: int, concurrent: int, served: int
) -> None:
    """Send `concurrent` calls from one key and count how many the gateway admitted.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory.
    index_dir : Path
        The fixture index.
    cap : int
        The `max_concurrent_per_token` override to build the app with.
    concurrent : int
        How many requests to send at once.
    served : int
        How many of them the cap should admit.
    """
    delay = 0.5

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
        max_concurrent_per_token=cap,
    )
    running = _served_by_uvicorn(app)
    port = next(running)
    base = f"http://127.0.0.1:{port}"
    ask = {"dataset": "gwhd", "row": 0}
    try:
        gate = threading.Barrier(concurrent)
        answers: list[int] = []
        lock = threading.Lock()

        def send() -> None:
            gate.wait()
            code = httpx.post(
                f"{base}/models/{MODEL}/predict", headers=KEY, json=ask, timeout=30
            ).status_code
            with lock:
                answers.append(code)

        senders = [threading.Thread(target=send) for _ in range(concurrent)]
        for sender in senders:
            sender.start()
        for sender in senders:
            sender.join(30)

        assert sorted(answers) == [200] * served + [429] * (concurrent - served)
    finally:
        next(running, None)


class _KeepAliveBackend(BaseHTTPRequestHandler):
    """A backend that speaks HTTP/1.1 and counts how many connections it accepted.

    Class attributes, because `BaseHTTPRequestHandler` is re-instantiated per request and the test
    needs to count what the *server* did, not what one handler instance saw.
    """

    protocol_version = "HTTP/1.1"
    accepted = 0
    close_after_response = False

    def setup(self) -> None:
        """Count the connection before the framework reads anything from it."""
        type(self).accepted += 1
        super().setup()

    def do_POST(self) -> None:
        """Answer one prediction, and die abruptly if the test asked for that."""
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = b'{"model": "sam3.1-meta", "objects": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        if type(self).close_after_response:
            # No `Connection: close` header — the peer simply goes away, which is what a keep-alive
            # window running out looks like to the client. This is the case a pool must survive.
            self.close_connection = True
            self.connection.close()

    def log_message(self, *args: Any) -> None:
        """Keep the request log off the test's stderr."""


def _serve_keepalive(close_after_response: bool) -> tuple[ThreadingHTTPServer, int]:
    """Start the counting backend on an ephemeral port and answer its port."""
    handler = type("Counting", (_KeepAliveBackend,), {})
    handler.accepted = 0
    handler.close_after_response = close_after_response
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_the_transport_reuses_one_connection_between_requests() -> None:
    """Two calls to one backend go over one socket, which is most of a call's latency.

    Measured against the live pool on 2026-10-17: the laboratory cards are 260 ms away, and the
    transport opened a TCP connection per call — so every request paid a handshake that overlapped
    with nothing when a participant called one image at a time.
    """
    server, port = _serve_keepalive(close_after_response=False)
    url = f"http://127.0.0.1:{port}/models/{MODEL}/predict"
    try:
        first = http_transport("POST", url, {}, b"{}", 5.0)
        second = http_transport("POST", url, {}, b"{}", 5.0)
        accepted = server.RequestHandlerClass.accepted
    finally:
        server.shutdown()
        server.server_close()

    assert (first.status, second.status) == (200, 200)
    assert accepted == 1, "the second call opened a new connection instead of reusing one"


def test_a_connection_the_peer_closed_is_retried_once_on_a_new_one() -> None:
    """A keep-alive window that ran out is not a failed backend.

    Without the retry, the first request after a quiet period fails — the cost of pooling paid as a
    new class of outage rather than as a saving.
    """
    server, port = _serve_keepalive(close_after_response=True)
    url = f"http://127.0.0.1:{port}/models/{MODEL}/predict"
    try:
        first = http_transport("POST", url, {}, b"{}", 5.0)
        time.sleep(0.05)
        second = http_transport("POST", url, {}, b"{}", 5.0)
        accepted = server.RequestHandlerClass.accepted
    finally:
        server.shutdown()
        server.server_close()

    assert (first.status, second.status) == (200, 200)
    assert accepted == 2, "the dead connection was not replaced"


def test_the_transport_disables_nagle_like_the_standard_library_does() -> None:
    """The overriding `connect` must keep `TCP_NODELAY`, or every call pays a delayed ACK.

    `HTTPConnection.connect` sets it; this class replaces that method to bound the handshake, and
    dropped the option with it. Measured on loopback 2026-10-17: 81 ms per call against 41 ms for a
    socket the standard library had configured, because a request's headers and body are two writes
    and the second waited for the acknowledgement of the first. On the 260 ms link to the laboratory
    backends the same wait is bounded by the round trip, which is why pooling exposed it.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    connection = _BoundedHTTPConnection("127.0.0.1", port, timeout=5.0)
    try:
        connection.connect()
        nodelay = connection.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    finally:
        connection.close()
        listener.close()

    assert nodelay == 1


def test_an_upload_that_is_not_an_image_never_reaches_a_backend(
    client: TestClient, backends: FakeBackends
) -> None:
    """Type is decided by the bytes, not by the `Content-Type` the client declares.

    An executable is inert here — the gateway never writes it to a path a shell could reach, and a
    backend only turns bytes into pixels — but a request that can never be an image should be
    refused where it arrives rather than after it has taken a backend's lane.
    """
    payload = b"#!/bin/sh\nrm -rf / # not an image, whatever the header says\n"
    response = client.post(
        f"/models/{MODEL}/predict/upload",
        headers=KEY,
        files={"file": ("photo.jpg", payload, "image/jpeg")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "JPEG" in response.json()["error"]["message"], "the refusal says what is accepted"
    assert backends.predict_calls() == [], "a non-image took a backend slot"


def test_an_upload_is_forwarded_with_the_type_its_bytes_are(
    client: TestClient, backends: FakeBackends
) -> None:
    """A backend is told what arrived, not what the client claimed it was."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 40
    response = client.post(
        f"/models/{MODEL}/predict/upload",
        headers=KEY,
        files={"file": ("photo.jpg", png, "image/jpeg")},
    )

    assert response.status_code == 200
    _, _, headers, body = backends.predict_calls()[0]
    assert headers["Content-Type"] == "image/png"
    assert body == png


def test_a_query_longer_than_the_cap_is_refused(client: TestClient) -> None:
    """The cap is on the whole string, because the whole string is what a backend receives.

    It replaced a per-parameter limit: with the parameter set belonging to the model, the gateway
    cannot know which name is the long one, and a cap on the transport is what the reason for the
    old field actually described.
    """
    response = client.post(
        f"/models/{MODEL}/predict",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
        params={"prompt": "w" * 5000},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


# --------------------------------------------------------------------------------------------
# The parameter contract: names, values, and combinations belong to the model, not to this package
# --------------------------------------------------------------------------------------------

ACCEPTED = ("prompt", "boxes", "points", "confidence_threshold", "imgsz")
"""What the full fixture model declares, in the order it declares it."""

PARAMETERS: list[dict[str, Any]] = [
    {"name": "prompt", "type": "text", "default": "wheat head", "max_length": 200},
    {"name": "boxes", "type": "boxes"},
    {"name": "points", "type": "points"},
    {
        "name": "confidence_threshold",
        "type": "number",
        "default": 0.5,
        "minimum": 0.0,
        "maximum": 1.0,
    },
    {"name": "imgsz", "type": "number", "values": [672, 784, 896, 1008], "above_maximum": "clamp"},
]
FULL_DECLARATION: dict[str, Any] = {**DECLARATION, "parameters": PARAMETERS}

SECOND_MODEL = "yolo-wheat"
"""A model whose parameter set shares no name with SAM's, which is the point of the mechanism."""

SECOND_DECLARATION: dict[str, Any] = {
    **DECLARATION,
    "model": SECOND_MODEL,
    "parameters": [
        {"name": "conf", "type": "number", "default": 0.25, "minimum": 0.0, "maximum": 1.0},
        {"name": "iou", "type": "number", "default": 0.7, "minimum": 0.0, "maximum": 1.0},
        {"name": "classes", "type": "text"},
    ],
}


def client_for(
    tmp_path: Path,
    index_dir: Path,
    declaration: dict[str, Any] | None,
    *,
    replies: Mapping[str, list[Reply]] | None = None,
    health: list[Reply] | None = None,
    models: Any = None,
) -> tuple[TestClient, FakeBackends]:
    """Build a client over a backend that declares this set of parameters.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory, for the token pool and the model list.
    index_dir : Path
        The fixture index addresses resolve through.
    declaration : dict or None
        What the backend answers at `/health`.
    replies : mapping of str to list of Reply, optional
        Programmed predict answers, per backend.
    health : list of Reply, optional
        Programmed declaration reads, which is how a test makes a backend unreadable.
    models : Any
        The model-to-backend list. `None` writes the fixture's two backends.

    Returns
    -------
    tuple of (TestClient, FakeBackends)
        The client and the transport, so a test can read what was forwarded.
    """
    backends = FakeBackends(replies=replies, declaration=declaration, health=health)
    return next(build_client(tmp_path, index_dir, backends, models=models)), backends


def test_an_undeclared_parameter_is_refused_and_the_accepted_ones_are_named(
    tmp_path: Path, index_dir: Path
) -> None:
    """A typo is the common case, and the answer says what would have worked."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict?promt=wheat", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "invalid_request"
    for name in ACCEPTED:
        assert name in body["message"], "a caller fixes a typo from the accepted list"
    assert backends.predict_calls() == [], "nothing undeclared reaches a machine"


def test_the_upload_route_refuses_the_same_way(tmp_path: Path, index_dir: Path) -> None:
    """Two routes, one contract: a parameter the model does not take is refused on both."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict/upload?promt=head",
        headers=KEY,
        files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0 bytes", "image/jpeg")},
    )

    assert response.status_code == 400
    assert "prompt" in response.json()["error"]["message"]
    assert backends.predict_calls() == []


def test_a_value_outside_the_declared_bounds_is_refused(tmp_path: Path, index_dir: Path) -> None:
    """The bound is the model's, read from its declaration, not a number in a signature."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict?confidence_threshold=2",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
    )

    assert response.status_code == 400
    assert "confidence_threshold" in response.json()["error"]["message"]
    assert backends.predict_calls() == []


def test_a_text_parameter_over_its_declared_length_is_refused(
    tmp_path: Path, index_dir: Path
) -> None:
    """A length the model declares is enforced by the gateway, and it is the model's number."""
    client, _ = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
        params={"prompt": "w" * 300},
    )

    assert response.status_code == 400
    assert "prompt" in response.json()["error"]["message"]


def test_a_model_that_declares_nothing_refuses_every_parameter(
    tmp_path: Path, index_dir: Path
) -> None:
    """An empty declaration is a model with no knobs, not a gateway with nothing to check."""
    client, _ = client_for(tmp_path, index_dir, {**DECLARATION, "parameters": []})

    response = client.post(
        f"/models/{MODEL}/predict?prompt=wheat", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 400


def test_a_model_whose_declaration_could_not_be_read_forwards_its_query(
    tmp_path: Path, index_dir: Path
) -> None:
    """A machine that answers but whose body cannot be read is not a model with no parameters.

    Refusing here would break a call that works: the container takes the parameter, the gateway
    merely failed to read the list. The published `declared` flag is what tells the two apart.
    """
    client, backends = client_for(
        tmp_path, index_dir, None, health=[Reply(status=200, body=b"not a declaration")]
    )

    listed = client.get("/models", headers=KEY).json()["models"][0]
    response = client.post(
        f"/models/{MODEL}/predict?prompt=wheat%20head&boxes=%5B%5B1%2C2%2C3%2C4%2C1%5D%5D",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
    )

    assert listed["declared"] is False
    assert listed["parameters"] == []
    assert response.status_code == 200
    assert backends.predict_calls()[0][1].endswith(
        "/predict?prompt=wheat%20head&boxes=%5B%5B1%2C2%2C3%2C4%2C1%5D%5D"
    )


def test_the_directory_says_whether_a_model_has_declared_anything(
    client: TestClient,
) -> None:
    """An empty parameter list because nothing answered is not one that is empty by design."""
    entry = client.get(f"/models/{MODEL}", headers=KEY).json()

    assert entry["declared"] is True
    assert entry["parameters"] == DECLARATION["parameters"]


def test_structured_parameters_are_forwarded_untouched(tmp_path: Path, index_dir: Path) -> None:
    """Boxes and points travel as one JSON value each, in the image's own pixel coordinates."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)
    query = "points=%5B%5B10%2C20%2C1%5D%2C%5B30%2C40%2C0%5D%5D&boxes=%5B%5B1%2C2%2C3%2C4%5D%5D"

    response = client.post(
        f"/models/{MODEL}/predict?{query}", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert backends.predict_calls()[0][1].endswith(f"/predict?{query}")


@pytest.mark.parametrize(
    "query",
    [
        "boxes=%5B1%2C2%2C3%2C4%5D",  # one box, not a list of boxes
        "boxes=%5B%5D",  # nothing to prompt with
        "boxes=%5B%5B1%2C2%2C3%5D%5D",  # three numbers
        "boxes=%5B%5B1%2C2%2C3%2C4%2C2%5D%5D",  # a label that is neither positive nor negative
        "boxes=%5B%5B1%2C2%2C3%2C%22x%22%5D%5D",  # not a number
        "boxes=not-json",
        "points=%5B%5B1%2C2%2C3%2C4%5D%5D",  # four numbers on a point
        "points=%5B%5B1%5D%5D",  # one number
    ],
)
def test_a_malformed_structured_value_is_refused(
    tmp_path: Path, index_dir: Path, query: str
) -> None:
    """The shape is checked here; what the points mean stays the backend's business."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict?{query}", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 400
    assert backends.predict_calls() == []


def test_a_size_above_the_ceiling_is_forwarded_for_the_container_to_clamp(
    tmp_path: Path, index_dir: Path
) -> None:
    """The ceiling is the container's to apply: it answers at the ceiling and says so."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict?imgsz=2000", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 200
    assert backends.predict_calls()[0][1].endswith("/predict?imgsz=2000")


def test_a_size_below_the_smallest_declared_one_is_refused(tmp_path: Path, index_dir: Path) -> None:
    """Below the smallest value the model was measured at is not a preference, it is a mistake."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict?imgsz=320", headers=KEY, json={"dataset": "gwhd", "row": 0}
    )

    assert response.status_code == 400
    assert "imgsz" in response.json()["error"]["message"]
    assert backends.predict_calls() == []


def test_a_parameter_repeated_is_refused(tmp_path: Path, index_dir: Path) -> None:
    """One name, one value. A list expanded into repeated names is a client bug, and it is named."""
    client, backends = client_for(tmp_path, index_dir, FULL_DECLARATION)

    response = client.post(
        f"/models/{MODEL}/predict?prompt=a&prompt=b",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
    )

    assert response.status_code == 400
    message = response.json()["error"]["message"]
    assert "once" in message or "JSON" in message
    assert backends.predict_calls() == []


def test_a_warning_from_a_backend_reaches_the_caller(tmp_path: Path, index_dir: Path) -> None:
    """A clamp is done, not refused, so the answer carries what was done and why."""
    clamped = {
        **PREDICTION,
        "resolution_used": 1008,
        "warnings": [
            {"code": "resolution_clamped", "message": "ran at 1008: the container's ceiling"}
        ],
    }
    client, _ = client_for(
        tmp_path,
        index_dir,
        FULL_DECLARATION,
        replies={FIRST: [Reply(status=200, body=json.dumps(clamped).encode())]},
    )

    body = client.post(
        f"/models/{MODEL}/predict?imgsz=2000", headers=KEY, json={"dataset": "gwhd", "row": 0}
    ).json()

    assert body["resolution_used"] == 1008
    assert body["warnings"] == clamped["warnings"]


def test_no_warning_key_when_the_backend_sent_none(tmp_path: Path, index_dir: Path) -> None:
    """The field is additive: an answer that needed no warning does not grow one."""
    client, _ = client_for(tmp_path, index_dir, FULL_DECLARATION)

    body = client.post(
        f"/models/{MODEL}/predict?imgsz=2000", headers=KEY, json={"dataset": "gwhd", "row": 0}
    ).json()

    assert "warnings" not in body, "the gateway relays the backend; it does not invent a warning"


def test_a_boolean_default_survives_the_declaration(tmp_path: Path, index_dir: Path) -> None:
    """A boolean is a boolean: `true` used to arrive as `1.0`, which no control can render."""
    declaration = {
        **DECLARATION,
        "parameters": [{"name": "recursive", "type": "boolean", "default": True}],
    }
    client, _ = client_for(tmp_path, index_dir, declaration)

    (parameter,) = client.get(f"/models/{MODEL}", headers=KEY).json()["parameters"]

    assert parameter["type"] == "boolean"
    assert parameter["default"] is True


def test_a_second_model_shape_validates_through_the_same_path(
    tmp_path: Path, index_dir: Path
) -> None:
    """No SAM name appears in the mechanism: a model with its own names is configured, not coded."""
    client, backends = client_for(
        tmp_path,
        index_dir,
        SECOND_DECLARATION,
        models={SECOND_MODEL: [FIRST, SECOND]},
    )

    accepted = client.post(
        f"/models/{SECOND_MODEL}/predict?conf=0.3&iou=0.6",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
    )
    refused = client.post(
        f"/models/{SECOND_MODEL}/predict?prompt=wheat",
        headers=KEY,
        json={"dataset": "gwhd", "row": 0},
    )

    assert accepted.status_code == 200
    assert backends.predict_calls()[0][1].endswith("/predict?conf=0.3&iou=0.6")
    assert refused.status_code == 400
    assert "conf" in refused.json()["error"]["message"]


def test_the_openapi_document_no_longer_names_a_parameter(tmp_path: Path, index_dir: Path) -> None:
    """The signature is the model's; listing `prompt` here would list it for every model."""
    client, _ = client_for(tmp_path, index_dir, FULL_DECLARATION)

    operation = client.app.openapi()["paths"]["/models/{model}/predict"]["post"]
    names = {parameter["name"] for parameter in operation.get("parameters", [])}

    assert "prompt" not in names
    assert "confidence_threshold" not in names
