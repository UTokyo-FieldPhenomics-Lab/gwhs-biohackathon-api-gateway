"""Repeated row predictions answered from a store, and the lanes a hit does not take.

The backend here is a fake that counts what reached it, because the property under test is not what
a model computes: it is how many times the gateway asked one, and whether a stored answer still
costs the participant a lane.
"""

from __future__ import annotations

import json
import logging
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from biohack_gateway.app import create_app
from biohack_gateway.cache import ResultCache, cache_key, canonical_request
from biohack_gateway.models import Reply
from biohack_gateway.tokens import TokenPool

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"
KEY = {"X-API-Key": TOKEN}
BASE = "https://data.example"

MODEL = "sam3.1-meta"
OTHER_MODEL = "gwfss-segformer-b4"
FIRST = "http://192.0.2.10:8000"
SECOND = "http://192.0.2.11:8000"
BACKEND_TOKEN = "backend-secret"

REVISION = "a" * 40
"""The revision the fixture index records; a test that changes it changes what is asked."""

DECLARATION: dict[str, Any] = {
    "model": MODEL,
    "resolution": 1008,
    "max_in_flight": 1,
    "parameters": [
        {"name": "prompt", "default": "wheat head"},
        {"name": "confidence_threshold", "default": 0.5, "minimum": 0.0, "maximum": 1.0},
    ],
}

COMPUTED_BY: dict[str, Any] = {
    "machine": "lab-3090",
    "gpu": "NVIDIA GeForce RTX 3090",
    "compute_capability": "8.6",
    "cuda": "12.8",
    "torch": "2.10.0+cu128",
}

PREDICTION: dict[str, Any] = {
    "model": MODEL,
    "resolution_used": 1008,
    "computed_by": COMPUTED_BY,
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


class FakeBackends:
    """Answer every request the way one machine would, and remember what was asked.

    Attributes
    ----------
    asked : list of str
        One URL per prediction request, in order, so a test can count how often a backend was
        reached — which is the whole of what "the cache answered" means from outside.
    """

    def __init__(self, *, slow_row: int | None = None, delay: float = 0.0) -> None:
        """Program the answers.

        Parameters
        ----------
        slow_row : int or None, optional
            A row whose image the backend takes `delay` seconds to answer about. It is how a test
            holds lanes open: the work in flight is real, not a sleep inserted in the gateway.
        delay : float
            How long that answer takes.
        """
        self.slow_row = slow_row
        self.delay = delay
        self.asked: list[str] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Reply:
        """Answer one request, the way a real backend would."""
        if url.endswith("/health"):
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        self.asked.append(url)
        if self.delay and self.slow_row is not None:
            image = f"{self.slow_row:08d}.jpg"
            if image in (body or b"").decode("utf-8", "replace"):
                time.sleep(self.delay)
        return Reply(status=200, body=json.dumps(PREDICTION).encode())

    def predictions(self) -> list[str]:
        """Return the prediction requests only, leaving the startup declarations out.

        Returns
        -------
        list of str
            The URLs the gateway forwarded, in order.
        """
        return list(self.asked)


def _cache(tmp_path: Path, *, max_bytes: int = 1 << 20) -> ResultCache:
    """Build a store of this test's own.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory.
    max_bytes : int, optional
        The cap, large enough that nothing is pruned unless the test asks for it.

    Returns
    -------
    ResultCache
        An enabled store, explicitly: the suite runs with the host's cache switched off, so a test
        that wants one has to say so.
    """
    return ResultCache(tmp_path / "cache.sqlite", max_bytes, enabled=True)


def _app(
    tmp_path: Path,
    index_dir: Path,
    backends: FakeBackends,
    cache: ResultCache | None,
    *,
    cap: int | None = None,
) -> FastAPI:
    """Build the application over the fixture index and the fake backends.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory.
    index_dir : Path
        The fixture index the addresses resolve through.
    backends : FakeBackends
        The transport standing in for the machines.
    cache : ResultCache or None
        The store to use, or `None` to let the environment decide, which is how the off switch is
        tested.
    cap : int or None, optional
        The per-token lane ceiling.

    Returns
    -------
    FastAPI
        The application, not yet serving.
    """
    pool = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(pool)
    models_path = tmp_path / "models.json"
    models_path.write_text(json.dumps({MODEL: [FIRST], OTHER_MODEL: [SECOND]}), encoding="utf-8")
    return create_app(
        tokens_path=pool,
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=models_path,
        backend_token=BACKEND_TOKEN,
        model_transport=backends,
        max_concurrent_per_token=cap,
        cache=cache,
    )


@contextmanager
def _client(
    tmp_path: Path,
    index_dir: Path,
    backends: FakeBackends,
    cache: ResultCache | None,
    *,
    cap: int | None = None,
) -> Iterator[TestClient]:
    """Serve that application to a `TestClient`, and close both when the block ends."""
    with TestClient(
        _app(tmp_path, index_dir, backends, cache, cap=cap), raise_server_exceptions=False
    ) as client:
        yield client


def _predict(
    client: TestClient, *, row: int, query: str = "", model: str = MODEL
) -> httpx.Response:
    """Ask one model about one published row."""
    return client.post(
        f"/models/{model}/predict{query}", headers=KEY, json={"dataset": "gwhd", "row": row}
    )


def test_a_second_identical_call_is_answered_without_a_backend(
    tmp_path: Path, index_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The same question twice reaches the model once, and the wire says the second was stored.

    A live answer carries no `cached` field at all — the route sends what was set — so a participant
    who never repeats a call sees exactly the body they saw before the cache existed. A hit writes
    no `inference` line either: nothing reached a machine.
    """
    backends = FakeBackends()
    with (
        caplog.at_level(logging.INFO, logger="biohack_gateway.models"),
        _client(tmp_path, index_dir, backends, _cache(tmp_path)) as client,
    ):
        first = _predict(client, row=0)
        second = _predict(client, row=0)

    assert (first.status_code, second.status_code) == (200, 200)
    assert len(backends.predictions()) == 1, "the second call reached a backend"
    assert "cached" not in first.json()
    assert "cached_at" not in first.json()
    assert second.json()["cached"] is True
    assert second.json()["cached_at"] is not None
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("inference ")
    ]
    assert len(lines) == 1, "a cache hit logged an inference it did not make"
    verdicts = [
        record.getMessage().split()[0]
        for record in caplog.records
        if record.getMessage().startswith("cache=")
    ]
    assert verdicts == ["cache=miss", "cache=hit"], "one line per predict says which path answered"


def test_a_hit_carries_the_record_of_the_machine_that_ran(tmp_path: Path, index_dir: Path) -> None:
    """`computed_by` is the backend's own record and is returned untouched by the store.

    It exists so a difference between two answers is explainable, which a cache that substituted
    its own provenance would destroy: the machine that ran is a fact about the answer, not about
    how it was delivered.
    """
    backends = FakeBackends()
    with _client(tmp_path, index_dir, backends, _cache(tmp_path)) as client:
        _predict(client, row=0)
        hit = _predict(client, row=0).json()

    assert hit["computed_by"] == COMPUTED_BY
    assert hit["objects"] == PREDICTION["objects"]
    assert hit["resolution_used"] == 1008


def test_the_same_parameters_in_another_order_are_the_same_question(
    tmp_path: Path, index_dir: Path
) -> None:
    """Ordering is normalized away, so a client cannot multiply entries by sorting its query."""
    backends = FakeBackends()
    with _client(tmp_path, index_dir, backends, _cache(tmp_path)) as client:
        first = _predict(client, row=0, query="?prompt=wheat&confidence_threshold=0.7")
        second = _predict(client, row=0, query="?confidence_threshold=0.7&prompt=wheat")

    assert first.status_code == 200, "the first call never stored anything"
    assert second.json()["cached"] is True
    assert len(backends.predictions()) == 1


@pytest.mark.parametrize("change", ["row", "query", "model"])
def test_another_question_is_a_miss(tmp_path: Path, index_dir: Path, change: str) -> None:
    """A different row, parameter or model is a different question and reaches a backend."""
    backends = FakeBackends()
    with _client(tmp_path, index_dir, backends, _cache(tmp_path)) as client:
        _predict(client, row=0)
        if change == "row":
            second = _predict(client, row=1)
        elif change == "query":
            second = _predict(client, row=0, query="?prompt=rust")
        else:
            second = _predict(client, row=0, model=OTHER_MODEL)

    assert second.status_code == 200
    assert "cached" not in second.json()
    assert len(backends.predictions()) == 2


def test_a_republished_dataset_is_a_miss(tmp_path: Path, index_dir: Path) -> None:
    """The revision is part of the key, so a republish makes the store cold by itself.

    The same app would never see a new revision — the index is read once — so this is what a
    restart after a publish does, with the same file on disk.
    """
    backends = FakeBackends()
    path = tmp_path / "cache.sqlite"
    with _client(tmp_path, index_dir, backends, ResultCache(path, 1 << 20, enabled=True)) as client:
        assert _predict(client, row=0).status_code == 200

    connection = sqlite3.connect(index_dir / "gwhd.db")
    try:
        connection.execute("UPDATE dataset SET revision = ?", ("b" * 40,))
        connection.commit()
    finally:
        connection.close()

    with _client(tmp_path, index_dir, backends, ResultCache(path, 1 << 20, enabled=True)) as client:
        republished = _predict(client, row=0)

    assert "cached" not in republished.json()
    assert len(backends.predictions()) == 2


def test_a_hit_answers_while_every_lane_is_busy(tmp_path: Path, index_dir: Path) -> None:
    """Three inferences in flight do not stop a fourth question the store can answer.

    This is the whole reason the lane moved out of the middleware: charging a stored answer a lane
    would refuse a participant for holding three, while the fourth answer costs no GPU at all.
    """
    backends = FakeBackends(slow_row=1, delay=0.6)
    app = _app(tmp_path, index_dir, backends, _cache(tmp_path), cap=3)
    served = _served_by_uvicorn(app)
    base = f"http://127.0.0.1:{next(served)}"
    try:
        warm = httpx.post(
            f"{base}/models/{MODEL}/predict",
            headers=KEY,
            json={"dataset": "gwhd", "row": 0},
            timeout=30,
        )
        assert warm.status_code == 200, "the answer was not stored before the burst"

        gate = threading.Barrier(4)  # the three senders and this thread
        held: list[int] = []
        lock = threading.Lock()

        def slow() -> None:
            gate.wait()
            code = httpx.post(
                f"{base}/models/{MODEL}/predict",
                headers=KEY,
                json={"dataset": "gwhd", "row": 1},
                timeout=30,
            ).status_code
            with lock:
                held.append(code)

        senders = [threading.Thread(target=slow) for _ in range(3)]
        for sender in senders:
            sender.start()
        gate.wait()
        time.sleep(0.15)  # all three lanes are now held by work inside the backend

        hit = httpx.post(
            f"{base}/models/{MODEL}/predict",
            headers=KEY,
            json={"dataset": "gwhd", "row": 0},
            timeout=30,
        )
        for sender in senders:
            sender.join(30)

        assert held == [200, 200, 200]
        assert hit.status_code == 200, "a stored answer was refused a lane it does not need"
        assert hit.json()["cached"] is True
    finally:
        next(served, None)


def test_stats_counts_entries_and_bytes_as_they_are_written_and_dropped(tmp_path: Path) -> None:
    """The figures `/pool` publishes are kept, not measured: the file can be gigabytes.

    Any participant key may call that route, so the alternative — a `SUM` over every stored body —
    turns a status read into a cold scan of the whole store.
    """
    cache = ResultCache(tmp_path / "cache.sqlite", 10_000, enabled=True)
    try:
        cache.put("a", "req", b"x" * 100)
        cache.put("b", "req", b"x" * 50)
        cache.put("a", "req", b"x" * 10)  # a replacement, not a second entry
        assert (cache.stats().entries, cache.stats().bytes) == (2, 60)

        cache.forget("a")
        assert (cache.stats().entries, cache.stats().bytes) == (1, 50)
    finally:
        cache.close()


def test_a_reopened_store_counts_what_was_already_there(tmp_path: Path) -> None:
    """The totals survive a restart because they are read from the file once, at open."""
    path = tmp_path / "cache.sqlite"
    first = ResultCache(path, 10_000, enabled=True)
    first.put("a", "req", b"x" * 40)
    first.put("b", "req", b"x" * 60)
    first.close()

    again = ResultCache(path, 10_000, enabled=True)
    try:
        assert (again.stats().entries, again.stats().bytes) == (2, 100)
    finally:
        again.close()


def test_pruning_respects_the_cap_and_keeps_the_newest(tmp_path: Path) -> None:
    """Pruning drops the oldest answers until the store fits, and keeps the newest whole."""
    cache = ResultCache(tmp_path / "cache.sqlite", 250, enabled=True)
    keys: list[str] = []
    for row in range(5):
        request = canonical_request(
            dataset="gwhd", revision=REVISION, row=row, model=MODEL, query=""
        )
        key = cache_key(request)
        cache.put(key, request, b"x" * 100)
        keys.append(key)
        time.sleep(0.002)  # a distinct computed_at per entry, so "oldest" is decidable
    assert cache.stats().bytes == 500

    cache.prune()
    stats = cache.stats()
    kept = [cache.get(key) is not None for key in keys]
    cache.close()

    assert stats.bytes <= 250
    assert stats.entries == 2, "the prune kept more than the cap allows"
    assert kept == [False, False, False, True, True], "the newest answers were not the ones kept"


def test_switching_the_cache_off_answers_every_call_from_the_backend(
    tmp_path: Path, index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`BIOHACK_CACHE=off` is a no-op store: every call forwards and no file is created.

    The host needs one variable and one restart to take the cache out of the answer path, and a
    disabled store must not even create the file it would have used.
    """
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("BIOHACK_CACHE", "off")
    monkeypatch.setenv("BIOHACK_CACHE_PATH", str(path))
    backends = FakeBackends()

    with _client(tmp_path, index_dir, backends, None) as client:
        first = _predict(client, row=0)
        second = _predict(client, row=0)

    assert (first.status_code, second.status_code) == (200, 200)
    assert "cached" not in second.json()
    assert len(backends.predictions()) == 2
    assert not path.exists(), "a switched-off cache created its file anyway"


def _served_by_uvicorn(app: Any) -> Iterator[int]:
    """Run the application on a real listening socket, the way the host does.

    Concurrency is the property under test, and `TestClient` answers one request per call, so it
    cannot show four requests holding lanes at once. A real server can.

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
