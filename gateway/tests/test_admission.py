"""Upload admission: a measured byte budget, a fair queue, and the numbers `/pool` publishes.

The queue is tested with a fake clock and, where a real wait is unavoidable, with a real thread
and a few dozen milliseconds — never with the twenty seconds a production refusal would take. The
route is tested through the real gate and the fake backends the other surface tests use, because
what matters outside this module is only what a participant sees: `429`, a `Retry-After`, and a row
that answered without waiting for anybody's photograph.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from biohack_gateway.admission import (
    DEFAULT_BYTES_PER_SECOND,
    DEFAULT_WAIT_SECONDS,
    MAX_BUDGET_BYTES,
    MAX_BYTES_PER_SECOND,
    MIN_BYTES_PER_SECOND,
    LinkRate,
    UploadQueue,
    UploadRefused,
)
from biohack_gateway.app import create_app
from biohack_gateway.cache import ResultCache
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
THIRD = "http://192.0.2.12:8000"
BACKEND_TOKEN = "backend-secret"
LAB_3090 = "lab-3090"
LAB_4090 = "lab-4090"

PNG = b"\x89PNG\r\n\x1a\n"
"""The header every fake upload starts with, so the route's magic check accepts it."""


class FakeClock:
    """A monotonic clock a test moves by hand, so nothing has to sleep to age a sample."""

    def __init__(self, now: float = 1000.0) -> None:
        """Start at an arbitrary positive stamp.

        Parameters
        ----------
        now : float, optional
            The starting reading. Any value works; a large one makes an accidental real clock
            obvious in a failure.
        """
        self.now = now

    def __call__(self) -> float:
        """Answer the current fake time, in the shape `time.monotonic` has."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward.

        Parameters
        ----------
        seconds : float
            How far.
        """
        self.now += seconds


class FakePool:
    """Answer for a pool of machines, declaring a machine name per container.

    `FIRST` and `SECOND` declare the same machine, so `lab-3090` carries two models; `THIRD` is a
    second machine carrying one. That is the grouping `/pool` has to reproduce.

    Attributes
    ----------
    delay : float
        How long a prediction takes, which is how a test holds an upload in flight.
    asked : list of str
        One URL per prediction request, in order.
    """

    def __init__(self, *, delay: float = 0.0) -> None:
        """Program the answers.

        Parameters
        ----------
        delay : float, optional
            Seconds each prediction sleeps before answering.
        """
        self.delay = delay
        self.asked: list[str] = []
        self._containers: dict[str, tuple[str, str]] = {
            FIRST: (MODEL, LAB_3090),
            SECOND: (OTHER_MODEL, LAB_3090),
            THIRD: (MODEL, LAB_4090),
        }

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Reply:
        """Answer one request the way a real container on a named machine would."""
        for address, (model, machine) in self._containers.items():
            if not url.startswith(address):
                continue
            if url.endswith("/health"):
                declaration = {
                    "model": model,
                    "resolution": 1008,
                    "max_in_flight": 1,
                    "computed_by": {"machine": machine},
                }
                return Reply(status=200, body=json.dumps(declaration).encode())
            self.asked.append(url)
            if self.delay:
                time.sleep(self.delay)
            return Reply(status=200, body=json.dumps({"model": model, "objects": []}).encode())
        return Reply(status=0, body=b"")


def _models_path(tmp_path: Path) -> Path:
    """Write the model-to-backend list the fake pool serves."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({MODEL: [FIRST, THIRD], OTHER_MODEL: [SECOND]}), encoding="utf-8")
    return path


def _app(
    tmp_path: Path,
    index_dir: Path,
    pool: FakePool,
    *,
    cache: ResultCache | None = None,
    admission: UploadQueue | None = None,
) -> FastAPI:
    """Build the application over the fixture index and the fake pool.

    Parameters
    ----------
    tmp_path : Path
        The test's temporary directory, which holds the token pool and the model list.
    index_dir : Path
        The fixture index the addresses resolve through.
    pool : FakePool
        The transport standing in for the machines.
    cache : ResultCache or None, optional
        The answer store; `None` lets the suite's switched-off cache apply.
    admission : UploadQueue or None, optional
        The queue to inject, so a test can saturate the link without moving bytes.

    Returns
    -------
    FastAPI
        The application, not yet serving.
    """
    tokens = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(tokens)
    return create_app(
        tokens_path=tokens,
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=_models_path(tmp_path),
        backend_token=BACKEND_TOKEN,
        model_transport=pool,
        cache=cache,
        admission=admission,
    )


@contextmanager
def _client(
    tmp_path: Path,
    index_dir: Path,
    pool: FakePool,
    *,
    cache: ResultCache | None = None,
    admission: UploadQueue | None = None,
) -> Iterator[TestClient]:
    """Serve that application to a `TestClient` and close both when the block ends."""
    with TestClient(
        _app(tmp_path, index_dir, pool, cache=cache, admission=admission),
        raise_server_exceptions=False,
    ) as client:
        yield client


def _saturated() -> UploadQueue:
    """Build a queue whose whole budget is already held, so nothing else fits.

    Returns
    -------
    UploadQueue
        A queue of ten bytes with ten in flight, and a wait of zero so a refusal is immediate.
    """
    queue = UploadQueue(max_budget_bytes=10, wait_seconds=0.0)
    assert queue.acquire("holder", 0, 10), "the empty link admits the blocker"
    return queue


def _upload(client: TestClient, payload: bytes = PNG + b"x" * 32) -> httpx.Response:
    """Send one small image to the upload route."""
    return client.post(
        f"/models/{MODEL}/predict/upload",
        headers=KEY,
        files={"file": ("wheat.png", payload, "image/png")},
    )


def _predict(client: TestClient, row: int) -> httpx.Response:
    """Ask one model about one published row."""
    return client.post(
        f"/models/{MODEL}/predict", headers=KEY, json={"dataset": "gwhd", "row": row}
    )


def test_the_link_rate_is_bytes_over_the_union_of_busy_seconds() -> None:
    """Two uploads in flight together cost one second of the link, not two.

    The union is the difference between a measurement of the link and a measurement of the average
    upload: summing durations would halve the estimate exactly when twelve streams are sharing it,
    which is when the budget matters.
    """
    clock = FakeClock()
    link = LinkRate(clock=clock)
    assert link.bytes_per_second == DEFAULT_BYTES_PER_SECOND, "the seed answers before any sample"

    link.record(seconds=2.0, completed_bytes=1_000_000)
    link.record(seconds=2.0, completed_bytes=1_000_000)
    assert link.bytes_per_second == pytest.approx(1_000_000.0), "two overlapping spans are one"

    clock.advance(61.0)
    assert link.bytes_per_second == DEFAULT_BYTES_PER_SECOND, "an expired sample is not evidence"


def test_the_link_rate_is_clamped_at_both_ends() -> None:
    """One impossible sample cannot open the gate, and one stalled one cannot close it."""
    fast = LinkRate(clock=FakeClock())
    fast.record(seconds=0.01, completed_bytes=100_000_000)
    assert fast.bytes_per_second == MAX_BYTES_PER_SECOND

    slow = LinkRate(clock=FakeClock())
    slow.record(seconds=10.0, completed_bytes=1_000)
    assert slow.bytes_per_second == MIN_BYTES_PER_SECOND

    nonsensical = LinkRate(clock=FakeClock())
    nonsensical.record(seconds=0.0, completed_bytes=1_000_000)
    nonsensical.record(seconds=1.0, completed_bytes=-5)
    assert nonsensical.bytes_per_second == DEFAULT_BYTES_PER_SECOND, "nothing usable was recorded"


def test_the_budget_is_twelve_seconds_of_the_measured_rate_under_a_cap() -> None:
    """The budget follows the estimate, and the byte cap still bounds it."""
    clock = FakeClock()
    link = LinkRate(clock=clock)
    assert UploadQueue(link).budget_bytes == 18_000_000, "the seed's twelve seconds"

    link.record(seconds=1.0, completed_bytes=1_000_000)
    assert UploadQueue(link).budget_bytes == 12_000_000

    link.record(seconds=1.0, completed_bytes=5_000_000)
    clock.advance(1.0)
    assert UploadQueue(link, max_budget_bytes=5_000_000).budget_bytes == 5_000_000
    assert UploadQueue(link).budget_bytes == MAX_BUDGET_BYTES, (
        "the rate would allow more than 24 MB"
    )


def test_the_queue_admits_what_fits_and_blocks_what_does_not() -> None:
    """A body is counted when it fits beside the bytes in flight, and refused when it does not.

    An empty link admits a body larger than the whole budget — otherwise a slow link would refuse a
    photograph forever — so the block is only meaningful once something is in flight.
    """
    queue = UploadQueue(max_budget_bytes=10, wait_seconds=0.0)
    assert queue.acquire("a", 0, 10) is True
    assert queue.stats().bytes_in_flight == 10
    assert queue.in_flight("a") == 10

    with pytest.raises(UploadRefused):
        queue.acquire("b", 0, 1)
    assert queue.stats().bytes_in_flight == 10, "a refused request was counted anyway"

    queue.release("a", 10, 0.001, 10)
    assert queue.stats().bytes_in_flight == 0
    assert queue.in_flight("a") == 0

    oversized = UploadQueue(max_budget_bytes=10, wait_seconds=0.0)
    assert oversized.acquire("a", 0, 11) is True, "an empty link admits one body of any size"


def test_a_timeout_carries_the_estimate_the_route_publishes() -> None:
    """The refusal's whole seconds are the drain of what is ahead, at the measured rate."""
    clock = FakeClock()
    link = LinkRate(clock=clock)
    link.record(seconds=1.0, completed_bytes=3_000_000)  # a measured 3 MB/s
    queue = UploadQueue(link, max_budget_bytes=3_000_000, wait_seconds=0.0, clock=clock)
    assert queue.acquire("a", 0, 3_000_000)

    started = time.monotonic()
    with pytest.raises(UploadRefused) as refused:
        queue.acquire("b", 0, 3_000_000)
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, "a zero wait must not sleep on the twenty-second deadline"
    assert refused.value.retry_after == pytest.approx(2.0), "6 MB of traffic at 3 MB/s"
    assert refused.value.retry_after_seconds == 2
    assert "retry in 2 s" in str(refused.value)


def test_a_wait_ends_at_its_deadline_with_the_estimate() -> None:
    """A wait that is allowed to run still ends, and still says when to come back."""
    queue = UploadQueue(max_budget_bytes=1_000, wait_seconds=0.05)
    assert queue.acquire("a", 0, 1_000)

    started = time.monotonic()
    with pytest.raises(UploadRefused) as refused:
        queue.acquire("b", 0, 1_000)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.04, "the wait ended before its deadline"
    assert elapsed < 2.0, "the wait did not respect its deadline"
    assert refused.value.retry_after > 0
    assert queue.stats().waiting == 0, "a timed-out waiter stayed in the queue"


@dataclass
class _Guest:
    """One waiting thread, and the two events the test uses to walk it through the queue."""

    admitted: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)


def _queued_waiter(
    queue: UploadQueue,
    *,
    label: str,
    team: str,
    team_in_flight: int,
    size: int,
    order: list[str],
    guests: dict[str, _Guest],
    lock: threading.Lock,
) -> threading.Thread:
    """Start a thread that waits for the link, records its admission, and holds it until told.

    Parameters
    ----------
    queue : UploadQueue
        The queue to wait on.
    label, team : str
        The name this test knows the waiter by, and the team it belongs to.
    team_in_flight : int
        The priority the queue sorts it by.
    size : int
        Its body's bytes.
    order : list of str
        Appended to, under `lock`, in the order admissions happened.
    guests : dict of str to _Guest
        The events for this waiter; the caller sets `finished` to let it release the link.
    lock : threading.Lock
        Guards `order`.

    Returns
    -------
    threading.Thread
        The started thread, which the caller joins.
    """

    def run() -> None:
        queue.acquire(team, team_in_flight, size)
        with lock:
            order.append(label)
        guests[label].admitted.set()
        assert guests[label].finished.wait(5.0), "the test never released this waiter"
        queue.release(team, size, 0.001, size)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _waiting_queue() -> tuple[UploadQueue, threading.Semaphore]:
    """Build a ten-byte queue and a semaphore that fires each time a request starts waiting."""
    started = threading.Semaphore(0)
    queue = UploadQueue(max_budget_bytes=10, wait_seconds=5.0, when_waiting=started.release)
    return queue, started


def _drain(semaphore: threading.Semaphore) -> None:
    """Consume the permits already handed out, so the next one belongs to the waiter under test."""
    while semaphore.acquire(blocking=False):
        pass


def test_the_queue_is_first_in_first_out_within_a_team() -> None:
    """Two requests from one team are served in the order they arrived."""
    queue, waiting = _waiting_queue()
    assert queue.acquire("holder", 0, 10)
    _drain(waiting)
    order: list[str] = []
    guests = {"first": _Guest(), "second": _Guest()}
    lock = threading.Lock()
    try:
        threads = [
            _queued_waiter(
                queue,
                label="first",
                team="a",
                team_in_flight=1,
                size=10,
                order=order,
                guests=guests,
                lock=lock,
            )
        ]
        assert waiting.acquire(timeout=5), "the first waiter never queued"
        threads.append(
            _queued_waiter(
                queue,
                label="second",
                team="a",
                team_in_flight=1,
                size=10,
                order=order,
                guests=guests,
                lock=lock,
            )
        )
        assert waiting.acquire(timeout=5), "the second waiter never queued"

        queue.release("holder", 10, 0.001, 10)
        assert guests["first"].admitted.wait(5)
        time.sleep(0.05)
        assert order == ["first"], "a later request of the same team overtook the head"

        guests["first"].finished.set()
        assert guests["second"].admitted.wait(5)
        assert order == ["first", "second"]
        guests["second"].finished.set()
    finally:
        for guest in guests.values():
            guest.finished.set()
        for thread in threads:
            thread.join(5)


def test_a_team_with_nothing_in_flight_overtakes_a_team_with_two() -> None:
    """The idle team is admitted ahead of two requests from a team already using the link."""
    queue, waiting = _waiting_queue()
    assert queue.acquire("holder", 0, 10)
    _drain(waiting)
    order: list[str] = []
    guests = {"busy": _Guest(), "idle": _Guest(), "busy-next": _Guest()}
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    try:
        for label, team, depth in (
            ("busy", "a", 2),
            ("idle", "b", 0),
            ("busy-next", "a", 2),
        ):
            threads.append(
                _queued_waiter(
                    queue,
                    label=label,
                    team=team,
                    team_in_flight=depth,
                    size=10,
                    order=order,
                    guests=guests,
                    lock=lock,
                )
            )
            assert waiting.acquire(timeout=5), f"{label} never queued"

        queue.release("holder", 10, 0.001, 10)
        assert guests["idle"].admitted.wait(5)
        time.sleep(0.05)
        assert order == ["idle"], "the idle team did not overtake the busy one"

        guests["idle"].finished.set()
        assert guests["busy"].admitted.wait(5)
        guests["busy"].finished.set()
        assert guests["busy-next"].admitted.wait(5)
        guests["busy-next"].finished.set()
        assert order == ["idle", "busy", "busy-next"]
    finally:
        for guest in guests.values():
            guest.finished.set()
        for thread in threads:
            thread.join(5)


def test_release_admits_exactly_one_waiter_and_it_is_the_head() -> None:
    """A release makes room for one body; the rest re-check and keep waiting."""
    queue, waiting = _waiting_queue()
    assert queue.acquire("holder", 0, 10)
    _drain(waiting)
    order: list[str] = []
    guests = {name: _Guest() for name in ("head", "middle", "tail")}
    lock = threading.Lock()
    threads: list[threading.Thread] = []
    try:
        for name in ("head", "middle", "tail"):
            threads.append(
                _queued_waiter(
                    queue,
                    label=name,
                    team=name,
                    team_in_flight=0,
                    size=10,
                    order=order,
                    guests=guests,
                    lock=lock,
                )
            )
            assert waiting.acquire(timeout=5), f"{name} never queued"

        queue.release("holder", 10, 0.001, 10)
        assert guests["head"].admitted.wait(5)
        time.sleep(0.05)
        assert order == ["head"], "more than one waiter was admitted by one release"
        stats = queue.stats()
        assert stats.bytes_in_flight == 10
        assert stats.waiting == 2, "the two behind the head left the queue"

        guests["head"].finished.set()
        assert guests["middle"].admitted.wait(5)
        guests["middle"].finished.set()
        assert guests["tail"].admitted.wait(5)
        guests["tail"].finished.set()
        assert order == ["head", "middle", "tail"]
    finally:
        for guest in guests.values():
            guest.finished.set()
        for thread in threads:
            thread.join(5)


def test_pool_publishes_the_machines_the_router_groups_and_the_cache(
    tmp_path: Path, index_dir: Path
) -> None:
    """`/pool` groups containers the way the router counts them, and reports the store beside it."""
    cache = ResultCache(tmp_path / "cache.sqlite", 1 << 20, enabled=True)
    with _client(tmp_path, index_dir, FakePool(), cache=cache) as client:
        assert _predict(client, row=0).status_code == 200
        assert _predict(client, row=0).status_code == 200
        response = client.get("/pool", headers=KEY)

    assert response.status_code == 200
    body = response.json()
    machines = {machine["machine"]: machine for machine in body["machines"]}
    assert set(machines) == {LAB_3090, LAB_4090}
    assert machines[LAB_3090]["models"] == [OTHER_MODEL, MODEL], "two containers, one card"
    assert machines[LAB_4090]["models"] == [MODEL]
    assert machines[LAB_3090]["alive"] is True
    assert machines[LAB_3090]["rtt_ms"] is not None
    assert machines[LAB_3090]["probed_seconds_ago"] == 0
    assert machines[LAB_3090]["in_flight"] == 0
    assert body["link"]["bytes_in_flight"] == 0
    assert body["link"]["bytes_per_s"] == pytest.approx(DEFAULT_BYTES_PER_SECOND)
    assert body["link"]["budget_bytes"] > 0
    assert body["link"]["waiting"] == 0
    assert body["cache"]["entries"] == 1
    assert body["cache"]["hits"] == 1
    assert body["cache"]["misses"] == 1
    assert body["cache"]["bytes"] > 0


def test_pool_is_keyed_and_outside_the_contract(tmp_path: Path, index_dir: Path) -> None:
    """`/pool` is an operator's route: it needs a token and it is not in the published reference."""
    with _client(tmp_path, index_dir, FakePool()) as client:
        refused = client.get("/pool")
        document = client.get("/openapi.json").json()

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "invalid_key"
    assert "/pool" not in document["paths"], "the operator's route is in the participant contract"


def test_a_row_never_waits_for_the_upload_queue(tmp_path: Path, index_dir: Path) -> None:
    """A saturated link is the uploads' problem: a row predict answers regardless.

    This is the pairing the whole design turns on — the cache and the thread pool absorb row
    traffic, and only bytes queue — so it is asserted by saturating the queue and asking a row and
    an upload in the same breath.
    """
    queue = _saturated()
    pool = FakePool()
    with _client(tmp_path, index_dir, pool, admission=queue) as client:
        started = time.monotonic()
        row = _predict(client, row=0)
        elapsed = time.monotonic() - started
        upload = _upload(client)

    assert row.status_code == 200, "a row was refused or held behind the byte queue"
    assert elapsed < 0.5, f"the row waited {elapsed:.2f} s for the upload queue"
    assert queue.stats().bytes_in_flight == 10, "the row took bytes it does not carry"
    assert upload.status_code == 429
    assert upload.json()["error"]["code"] == "quota_exceeded"
    assert "retry in" in upload.json()["error"]["message"]
    assert int(upload.headers["Retry-After"]) >= 1
    assert upload.headers["Retry-After"] == str(int(upload.headers["Retry-After"]))
    assert len(pool.asked) == 1, "the refused upload reached a backend"


def test_an_admitted_upload_counts_its_bytes_then_returns_them(
    tmp_path: Path, index_dir: Path
) -> None:
    """A served upload is counted while it runs and released when the answer is in."""
    queue = UploadQueue(max_budget_bytes=10, wait_seconds=0.0)
    assert queue.acquire("holder", 0, 10)
    with _client(tmp_path, index_dir, FakePool(), admission=queue) as client:
        queue.release("holder", 10, 0.001, 10)
        served = _upload(client)
        stats = queue.stats()

    assert served.status_code == 200
    assert stats.bytes_in_flight == 0, "the bytes were not released"
    assert stats.bytes_per_team == {}
    assert queue.link.bytes_per_second >= MIN_BYTES_PER_SECOND


def test_pool_shows_the_upload_in_flight(tmp_path: Path, index_dir: Path) -> None:
    """An upload being served is visible as a depth and as bytes, and both drain afterwards.

    The link is one, so the byte figure is reported on every machine row; what this checks is that
    the number is the queue's live count, not a figure written once.
    """
    pool = FakePool(delay=0.4)
    app = _app(tmp_path, index_dir, pool)
    served = _served(app)
    base = f"http://127.0.0.1:{next(served)}"
    payload = PNG + b"x" * 1024
    try:
        sender = threading.Thread(
            target=lambda: httpx.post(
                f"{base}/models/{MODEL}/predict/upload",
                headers=KEY,
                files={"file": ("wheat.png", payload, "image/png")},
                timeout=30,
            ),
            daemon=True,
        )
        sender.start()
        busy = _poll_pool(base, lambda body: body["link"]["bytes_in_flight"] > 0)
        sender.join(30)

        assert busy is not None, "the in-flight upload never appeared in /pool"
        assert any(machine["in_flight"] == 1 for machine in busy["machines"])
        assert busy["link"]["bytes_in_flight"] == len(payload)

        drained = _poll_pool(base, lambda body: body["link"]["bytes_in_flight"] == 0)
        assert drained is not None, "the upload's bytes were never released"
        assert all(machine["in_flight"] == 0 for machine in drained["machines"])
    finally:
        next(served, None)


def _poll_pool(base: str, until: Any, timeout: float = 5.0) -> dict[str, Any] | None:
    """Poll `/pool` until `until` says the body is interesting, or the timeout passes.

    Parameters
    ----------
    base : str
        The server's origin.
    until : callable
        A predicate over the decoded body.
    timeout : float, optional
        How long to keep asking.

    Returns
    -------
    dict or None
        The first body that satisfied the predicate, or `None` if none did.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body: dict[str, Any] = httpx.get(f"{base}/pool", headers=KEY, timeout=5).json()
        if until(body):
            return body
        time.sleep(0.02)
    return None


def _served(app: FastAPI) -> Iterator[int]:
    """Run the application on a real listening socket, the way the host does.

    Parameters
    ----------
    app : FastAPI
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


def test_the_budget_and_the_wait_can_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host sets the cap and the window with two variables, and a zero wait means "refuse now"."""
    monkeypatch.setenv("BIOHACK_UPLOAD_BYTES", "1234")
    monkeypatch.setenv("BIOHACK_UPLOAD_WAIT_SECONDS", "0")
    queue = UploadQueue()
    assert DEFAULT_WAIT_SECONDS == 20.0, "the shipped wait is the plan's twenty seconds"
    assert queue.budget_bytes == 1234
    assert queue.acquire("a", 0, 1234)
    started = time.monotonic()
    with pytest.raises(UploadRefused):
        queue.acquire("b", 0, 1)
    assert time.monotonic() - started < 0.5, "the environment's zero wait was not honoured"


def test_the_upload_queue_create_app_was_given_is_the_one_used(
    tmp_path: Path, index_dir: Path
) -> None:
    """One way to configure admission: the queue passed in is the queue the route waits on."""
    tokens = tmp_path / "tokens.json"
    TokenPool({TEAM: TOKEN}).save(tokens)
    queue = UploadQueue(max_budget_bytes=10, wait_seconds=0.0)
    app = create_app(
        tokens_path=tokens,
        index_dir=index_dir,
        data_base_url=BASE,
        models_path=_models_path(tmp_path),
        backend_token=BACKEND_TOKEN,
        model_transport=FakePool(),
        admission=queue,
    )
    assert queue.budget_bytes == 10
    assert queue.acquire("holder", 0, 10)

    with TestClient(app, raise_server_exceptions=False) as client:
        refused = _upload(client)

    assert refused.status_code == 429, "the configured budget did not bound the upload"
    assert int(refused.headers["Retry-After"]) >= 1
