"""Upload resources: a slot per parsed body, and no payload before admission.

The host this serves has 1,936 MiB of RAM and no swap, and its `/tmp` is a 969 MiB tmpfs. A
multipart body is spooled there by the parser, before the handler runs, so how many bodies may
exist at once is a resource in its own right; the handler's payload is a second copy of the same
body in the heap, so it must not be made before admission has said it fits. These tests pin both
halves of that boundary: the middleware's global slot around the whole parse-to-answer lifetime,
and the handler's use of the parsed size before it reads a byte.

Nothing here moves a megabyte: the byte queue, the backend transport, and the slot capacity are
injected, so a refusal is reached without a load test.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import anyio.to_thread
import httpx
import pytest
import uvicorn
from fastapi import HTTPException, Request, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from biohack_gateway.admission import (
    DEFAULT_UPLOAD_SLOTS,
    UPLOAD_SLOTS_ENV,
    UploadQueue,
    UploadRefused,
    UploadSlots,
)
from biohack_gateway.app import create_app
from biohack_gateway.models import Reply
from biohack_gateway.tokens import TokenPool

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"
KEY = {"X-API-Key": TOKEN}
BASE = "https://data.example"
MODEL = "sam3.1-meta"
BACKEND = "http://192.0.2.10:8000"
BACKEND_TOKEN = "backend-secret"

PNG = b"\x89PNG\r\n\x1a\n"
"""The header the route's magic check accepts, so a small test body is a photograph to it."""

DECLARATION = {
    "model": MODEL,
    "resolution": 1008,
    "max_in_flight": 1,
    "computed_by": {"machine": "lab-3090"},
}
"""What the fake container publishes about itself; the probe asks for it during a lifespan."""

PREDICTION = {"model": MODEL, "objects": []}
"""The smallest answer this contract accepts."""


class FakeBackend:
    """Answer one configured container, with an optional delay or a silent send.

    Attributes
    ----------
    delay : float
        Seconds a prediction sleeps, which is how a test holds an upload in flight.
    silent : bool
        Answer a prediction as a send that timed out, so the route's failure path is reached without
        a real socket.
    bodies : list of bytes
        One entry per prediction body received, in order.
    predictions : int
        How many prediction requests arrived, including ones that failed.
    current : int
        How many predictions are being answered right now.
    peak : int
        The most that were ever answered at once, which is how a slot bound is observed from the
        backend's side rather than from the pool's.
    """

    def __init__(self, *, delay: float = 0.0, silent: bool = False) -> None:
        """Program the answers.

        Parameters
        ----------
        delay : float, optional
            Seconds each prediction sleeps before answering.
        silent : bool, optional
            Whether a prediction answers as a send timeout instead of a body.
        """
        self.delay = delay
        self.silent = silent
        self.bodies: list[bytes] = []
        self.predictions = 0
        self.current = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Reply:
        """Answer one request the way the container behind the model would."""
        if url.endswith("/health"):
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        with self._lock:
            self.predictions += 1
            self.current += 1
            self.peak = max(self.peak, self.current)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.silent:
                return Reply(status=0, body=b"", timed_out=True, stage="send")
            if body is not None:
                self.bodies.append(body)
            return Reply(status=200, body=json.dumps(PREDICTION).encode())
        finally:
            with self._lock:
                self.current -= 1


class RecordingQueue:
    """Stand in for the byte queue and remember the order of the boundary's decisions.

    It is a seam rather than the real queue because the property under test is an *ordering*: the
    body is read after admission and not before. A real queue would hide which call came first.

    Attributes
    ----------
    events : list of str
        ``acquire``, ``read`` and ``release`` as they happen, so an ordering can be compared whole.
    sizes : list of int
        The bytes each admission counted.
    released : list of tuple
        ``(size, completed)`` per release, which is what a `finally` has to balance.
    """

    def __init__(self) -> None:
        """Start with nothing admitted and nothing recorded."""
        self.events: list[str] = []
        self.sizes: list[int] = []
        self.released: list[tuple[int, int]] = []

    def acquire(self, team: str, team_in_flight: int, size: int) -> bool:
        """Record an admission and always make room, so the handler's own guards are what decide."""
        self.events.append("acquire")
        self.sizes.append(size)
        return True

    def release(self, team: str, size: int, seconds: float, completed_bytes: int) -> None:
        """Record the release that a `finally` owes for the admission above."""
        self.events.append("release")
        self.released.append((size, completed_bytes))


class Spool(BytesIO):
    """A body stream that refuses to be read before the queue has admitted it.

    The assertion is the test: today's regression is a handler that read first and admitted after,
    and a stream that fails on that order is a more direct witness than a memory measurement.
    """

    def __init__(self, data: bytes, queue: RecordingQueue) -> None:
        """Wrap a body in a stream that watches the queue.

        Parameters
        ----------
        data : bytes
            The body the parser would have spooled.
        queue : RecordingQueue
            The admissions already recorded, which the read is compared against.
        """
        super().__init__(data)
        self._queue = queue

    def read(self, size: int = -1) -> bytes:
        """Read the body, but only once it has been admitted.

        Returns
        -------
        bytes
            The remaining body.
        """
        assert self._queue.events[-1:] == ["acquire"], "the payload was read before it was admitted"
        self._queue.events.append("read")
        return super().read(size)


def _models_path(tmp_path: Path) -> Path:
    """Write the model-to-backend list the gateway loads."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({MODEL: [BACKEND]}), encoding="utf-8")
    return path


def _app(
    tmp_path: Path,
    index_dir: Path,
    backend: FakeBackend,
    *,
    admission: UploadQueue | None = None,
    upload_slots: UploadSlots | None = None,
    max_concurrent_per_token: int | None = None,
) -> Any:
    """Build the application over the fixture index and one fake container.

    Parameters
    ----------
    tmp_path : Path
        The test's directory, which holds the token pool and the model list.
    index_dir : Path
        The fixture index the dataset surface answers from.
    backend : FakeBackend
        The transport standing in for the machine.
    admission : UploadQueue or None, optional
        The byte queue; `None` builds the default one.
    upload_slots : UploadSlots or None, optional
        The global parse-to-answer slot pool; `None` builds one from the environment.
    max_concurrent_per_token : int or None, optional
        The per-token lane ceiling. `0` takes it out of the picture, so a test about the global
        slots is not measuring the fairness cap.

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
        model_transport=backend,
        max_concurrent_per_token=max_concurrent_per_token,
        admission=admission,
        upload_slots=upload_slots,
    )


@contextmanager
def _client(
    tmp_path: Path,
    index_dir: Path,
    backend: FakeBackend,
    *,
    admission: UploadQueue | None = None,
    upload_slots: UploadSlots | None = None,
) -> Iterator[TestClient]:
    """Serve that application to a `TestClient` and close both when the block ends."""
    with TestClient(
        _app(
            tmp_path,
            index_dir,
            backend,
            admission=admission,
            upload_slots=upload_slots,
        ),
        raise_server_exceptions=False,
    ) as client:
        yield client


def _upload(client: TestClient, payload: bytes = PNG + b"x" * 32) -> httpx.Response:
    """Send one small image to the upload route through the whole application."""
    return client.post(
        f"/models/{MODEL}/predict/upload",
        headers=KEY,
        files={"file": ("wheat.png", payload, "image/png")},
    )


def _post_upload(base: str, payload: bytes = PNG + b"x" * 32) -> httpx.Response:
    """Send one upload to a served application, from a thread of its own."""
    return httpx.post(
        f"{base}/models/{MODEL}/predict/upload",
        headers=KEY,
        files={"file": ("wheat.png", payload, "image/png")},
        timeout=30,
    )


def _served(app: Any) -> Iterator[int]:
    """Run the application on a real listening socket, the way the host does.

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


def _await(until: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Poll until a condition holds, without assuming how fast the server got there.

    Parameters
    ----------
    until : callable
        The condition, which must be cheap and side-effect free.
    timeout : float, optional
        How long to keep asking.

    Returns
    -------
    bool
        Whether it held before the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until():
            return True
        time.sleep(0.02)
    return until()


def _endpoint(app: Any) -> Callable[..., Any]:
    """Find the upload route's handler so a test can call it with a body of its own choosing.

    A router included into the application keeps its routes behind a wrapper rather than flattening
    them, so the search walks what it finds instead of assuming one level.
    """
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        if getattr(route, "path", None) == "/models/{model}/predict/upload":
            return route.endpoint  # type: ignore[no-any-return]
        nested = getattr(route, "routes", None)
        if nested is None:
            nested = getattr(getattr(route, "original_router", None), "routes", None)
        if nested:
            pending.extend(nested)
    raise AssertionError("the upload route is not registered")


def _direct_request() -> Request:
    """Build the request a handler sees, with the team the gate would have recorded."""
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "path": f"/models/{MODEL}/predict/upload",
        "query_string": b"",
        "headers": [],
        "app": None,
    }
    request = Request(scope)
    request.state.team_id = TEAM
    return request


def _upload_file(data: bytes, queue: RecordingQueue) -> UploadFile:
    """Build the parsed part the handler receives, with the size the parser would have counted."""
    return UploadFile(
        file=Spool(data, queue),
        size=len(data),
        filename="wheat.png",
        headers=Headers({"content-type": "image/png"}),
    )


def test_the_slot_pool_counts_and_refuses_rather_than_waits() -> None:
    """A slot is taken or refused at once; nothing is queued and a refusal counts nothing."""
    pool = UploadSlots(capacity=2)
    assert pool.capacity == 2
    assert pool.in_flight() == 0

    pool.acquire()
    pool.acquire()
    assert pool.in_flight() == 2

    with pytest.raises(UploadRefused) as refused:
        pool.acquire()
    assert refused.value.retry_after_seconds >= 1
    assert "retry in" in str(refused.value)
    assert pool.in_flight() == 2, "a refusal took a slot it has to give back"

    pool.release()
    pool.acquire()
    pool.release()
    pool.release()
    pool.release()
    assert pool.in_flight() == 0, "a release below zero left the pool closed"


def test_a_slot_capacity_that_can_hold_nothing_is_a_configuration_error() -> None:
    """Zero slots would refuse every upload, which is a host mistake rather than a way to serve."""
    for capacity in (0, -1):
        with pytest.raises(ValueError):
            UploadSlots(capacity=capacity)


def test_the_slot_pool_is_configured_by_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host sets the count with one variable, and a typo falls back to the shipped number."""
    assert DEFAULT_UPLOAD_SLOTS == 8, "the shipped slot count is the measured one"
    assert UploadSlots().capacity == DEFAULT_UPLOAD_SLOTS

    monkeypatch.setenv(UPLOAD_SLOTS_ENV, "3")
    assert UploadSlots().capacity == 3

    for raw in ("0", "-2", "eight", ""):
        monkeypatch.setenv(UPLOAD_SLOTS_ENV, raw)
        assert UploadSlots().capacity == DEFAULT_UPLOAD_SLOTS, raw


def _worker_threads() -> int:
    """Read the worker pool's own size, on a loop of its own, as AnyIO builds it."""

    async def read() -> int:
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    return asyncio.run(read())


def test_parked_uploads_cannot_occupy_the_worker_pool() -> None:
    """The slots are what bounds parked uploads, so the process keeps the framework's 40 workers.

    An upload waits for the link inside a `def` handler, which holds one AnyIO worker. The pool was
    once raised to 200 to fit that waiting, which bounded no memory at all; the slot pool does, and
    the workers stay at the framework's number so metadata keeps threads of its own.
    """
    workers = _worker_threads()
    assert workers == 40, "the worker pool is no longer the framework's own number"
    assert workers > DEFAULT_UPLOAD_SLOTS, "parked uploads could take every worker"


def test_a_full_slot_pool_answers_429_with_a_retry_after(tmp_path: Path, index_dir: Path) -> None:
    """The refusal is the middleware's, immediate, and the slot is reusable once it frees."""
    backend = FakeBackend(delay=0.4)
    slots = UploadSlots(capacity=1)
    served = _served(_app(tmp_path, index_dir, backend, upload_slots=slots))
    base = f"http://127.0.0.1:{next(served)}"
    first: list[httpx.Response] = []
    try:
        sender = threading.Thread(target=lambda: first.append(_post_upload(base)), daemon=True)
        sender.start()
        assert _await(lambda: slots.in_flight() == 1), "the admitted upload never took a slot"

        refused = _post_upload(base)
        sender.join(30)

        assert refused.status_code == 429
        assert refused.json()["error"]["code"] == "quota_exceeded"
        assert int(refused.headers["Retry-After"]) >= 1
        assert backend.predictions == 1, "the refused upload reached a backend"
        assert [answer.status_code for answer in first] == [200]
        assert slots.in_flight() == 0, "the served upload never gave its slot back"

        assert _post_upload(base).status_code == 200, "the slot was not reusable after release"
    finally:
        next(served, None)


def test_a_refused_upload_is_never_parsed(tmp_path: Path, index_dir: Path) -> None:
    """The slot is checked before the parser, which is the whole point of taking it here.

    A body that is not multipart is refused `400` by the parser when a slot is free and `429` when
    none is: the second answer can only happen if admission decided before a byte was parsed, and
    it is also the release path a parse failure takes.
    """
    backend = FakeBackend(delay=0.4)
    slots = UploadSlots(capacity=1)
    served = _served(_app(tmp_path, index_dir, backend, upload_slots=slots))
    base = f"http://127.0.0.1:{next(served)}"
    malformed = {"content-type": "multipart/form-data; boundary=not-a-body"} | KEY
    sender = threading.Thread(target=lambda: _post_upload(base), daemon=True)
    try:
        sender.start()
        assert _await(lambda: slots.in_flight() == 1), "the admitted upload never took a slot"
        refused = httpx.post(
            f"{base}/models/{MODEL}/predict/upload",
            headers=malformed,
            content=b"this is not multipart",
            timeout=30,
        )
        sender.join(30)

        assert refused.status_code == 429, "a refused body was parsed before admission"
        assert slots.in_flight() == 0

        parsed = httpx.post(
            f"{base}/models/{MODEL}/predict/upload",
            headers=malformed,
            content=b"this is not multipart",
            timeout=30,
        )
        assert parsed.status_code == 400, "the malformed body should fail parsing once admitted"
        assert slots.in_flight() == 0, "a parse failure kept its slot"
    finally:
        next(served, None)


def test_the_served_app_takes_its_slot_count_from_the_environment(
    tmp_path: Path, index_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The variable a host sets is the limit the served gateway enforces.

    Nothing is injected here: the application reads `BIOHACK_UPLOAD_SLOTS`, and the backend's own
    concurrency counter is the witness, so this is the check an operator can repeat on a host.
    """
    monkeypatch.setenv(UPLOAD_SLOTS_ENV, "2")
    backend = FakeBackend(delay=0.4)
    served = _served(_app(tmp_path, index_dir, backend))
    base = f"http://127.0.0.1:{next(served)}"
    answers: list[httpx.Response] = []
    lock = threading.Lock()

    def send() -> None:
        answer = _post_upload(base)
        with lock:
            answers.append(answer)

    senders = [threading.Thread(target=send, daemon=True) for _ in range(6)]
    try:
        for sender in senders:
            sender.start()
        assert _await(lambda: backend.peak == 2), "the configured two slots were not both used"
        for sender in senders:
            sender.join(30)
    finally:
        next(served, None)

    assert backend.peak == 2, f"{backend.peak} uploads ran at once against a limit of two"
    assert backend.predictions == 2, "an upload that never got a slot still reached a backend"
    assert len(answers) == 6
    refused = [answer for answer in answers if answer.status_code == 429]
    assert refused, "a burst deeper than the configured slots was not refused"
    for answer in refused:
        assert int(answer.headers["Retry-After"]) >= 1
        assert "upload slot" in answer.json()["error"]["message"]


def test_the_slot_is_released_when_the_route_refuses_the_body(
    tmp_path: Path, index_dir: Path
) -> None:
    """An empty upload is refused inside the handler, and the slot it held comes straight back."""
    slots = UploadSlots(capacity=1)
    with _client(tmp_path, index_dir, FakeBackend(), upload_slots=slots) as client:
        empty = _upload(client, b"")
        assert empty.status_code == 400
        assert slots.in_flight() == 0, "a refused upload kept its slot"
        assert _upload(client).status_code == 200


def test_the_slot_is_released_when_every_backend_is_gone(tmp_path: Path, index_dir: Path) -> None:
    """A failed forward is an exception path too, and the slot belongs on the way out of it."""
    slots = UploadSlots(capacity=1)
    with _client(tmp_path, index_dir, FakeBackend(silent=True), upload_slots=slots) as client:
        failed = _upload(client)
    assert failed.status_code == 504
    assert slots.in_flight() == 0, "an upload whose backend failed held its slot"


def test_the_body_is_admitted_by_its_parsed_size_and_read_afterwards(
    tmp_path: Path, index_dir: Path
) -> None:
    """The ordering is the fix: the parsed length is admitted, and only then is the body read."""
    queue = RecordingQueue()
    backend = FakeBackend()
    app = _app(tmp_path, index_dir, backend, admission=queue)  # type: ignore[arg-type]
    body = PNG + b"y" * 64

    answer = _endpoint(app)(
        model=MODEL,
        request=_direct_request(),
        file=_upload_file(body, queue),
    )

    assert queue.events == ["acquire", "read", "release"], "the boundary changed order"
    assert queue.sizes == [len(body)], "admission counted something other than the parsed size"
    assert queue.released == [(len(body), len(body) + len(json.dumps(PREDICTION)))], (
        "the release did not balance the admission and its answer"
    )
    assert backend.bodies == [body]
    assert answer.model == MODEL


def test_an_empty_upload_is_refused_before_it_is_read(tmp_path: Path, index_dir: Path) -> None:
    """A parsed size of zero is a refusal that never touches the stream or the queue."""
    queue = RecordingQueue()
    app = _app(tmp_path, index_dir, FakeBackend(), admission=queue)  # type: ignore[arg-type]
    spool = Spool(b"", queue)
    empty = UploadFile(
        file=spool,
        size=0,
        filename="wheat.png",
        headers=Headers({"content-type": "image/png"}),
    )

    with pytest.raises(HTTPException) as refused:
        _endpoint(app)(model=MODEL, request=_direct_request(), file=empty)

    assert refused.value.status_code == 400
    assert queue.events == [], "an empty upload was admitted"
    assert spool.tell() == 0, "an empty upload was read"


def test_a_body_without_a_parsed_size_is_refused_rather_than_measured(
    tmp_path: Path, index_dir: Path
) -> None:
    """No size means admission cannot be computed, so the body is not read to find one.

    The client's `Content-Length` is never the answer: it describes what was sent rather than what
    the parser wrote, and a stream whose length cannot be known before it is in memory is exactly
    the request this boundary exists to refuse.
    """
    queue = RecordingQueue()
    app = _app(tmp_path, index_dir, FakeBackend(), admission=queue)  # type: ignore[arg-type]
    spool = Spool(PNG + b"z" * 16, queue)
    unknown = UploadFile(file=spool, size=None, filename="wheat.png")

    with pytest.raises(HTTPException) as refused:
        _endpoint(app)(model=MODEL, request=_direct_request(), file=unknown)

    assert refused.value.status_code == 400
    assert queue.events == [], "an unmeasurable body was admitted"
    assert spool.tell() == 0, "an unmeasurable body was read to measure it"


def test_a_failed_read_gives_the_admitted_bytes_back(tmp_path: Path, index_dir: Path) -> None:
    """A spool that cannot be read is a `503` and a release, not a leak in the byte budget."""

    class Broken(BytesIO):
        """A spool whose read fails after admission, as a discarded temporary file would."""

        def read(self, size: int = -1) -> bytes:
            """Fail the way a file that is gone under the process fails."""
            raise OSError("the spool is gone")

    queue = UploadQueue(max_budget_bytes=1_000, wait_seconds=0.0)
    app = _app(tmp_path, index_dir, FakeBackend(), admission=queue)
    broken = UploadFile(file=Broken(b""), size=32, filename="wheat.png")

    with pytest.raises(HTTPException) as refused:
        _endpoint(app)(model=MODEL, request=_direct_request(), file=broken)

    assert refused.value.status_code == 503
    assert "could not be read" in str(refused.value.detail)
    assert queue.stats().bytes_in_flight == 0, "a failed read kept the bytes it was admitted for"
    assert queue.in_flight(TEAM) == 0
    assert queue.stats().waiting == 0


def test_a_declared_size_that_reads_empty_gives_the_bytes_back(
    tmp_path: Path, index_dir: Path
) -> None:
    """A spool that lost its bytes after parsing is still an empty upload, and still a release."""
    queue = UploadQueue(max_budget_bytes=1_000, wait_seconds=0.0)
    app = _app(tmp_path, index_dir, FakeBackend(), admission=queue)
    short = UploadFile(file=BytesIO(b""), size=16, filename="wheat.png")

    with pytest.raises(HTTPException) as refused:
        _endpoint(app)(model=MODEL, request=_direct_request(), file=short)

    assert refused.value.status_code == 400
    assert "empty" in str(refused.value.detail)
    assert queue.stats().bytes_in_flight == 0, "an empty body kept the bytes it was admitted for"


def test_a_burst_of_uploads_leaves_the_worker_pool_for_metadata(
    tmp_path: Path, index_dir: Path
) -> None:
    """A burst deeper than the slots is refused, and a dataset listing still answers meanwhile."""
    backend = FakeBackend(delay=0.4)
    slots = UploadSlots(capacity=DEFAULT_UPLOAD_SLOTS)
    # The per-token lane is taken out of the picture: this test is about the global slots, and a
    # team refused at three lanes never reaches the burst the slots are meant to bound.
    served = _served(
        _app(
            tmp_path,
            index_dir,
            backend,
            upload_slots=slots,
            max_concurrent_per_token=0,
        )
    )
    base = f"http://127.0.0.1:{next(served)}"
    statuses: list[int] = []
    lock = threading.Lock()

    def send() -> None:
        answer = _post_upload(base)
        with lock:
            statuses.append(answer.status_code)

    senders = [threading.Thread(target=send, daemon=True) for _ in range(DEFAULT_UPLOAD_SLOTS + 4)]
    try:
        for sender in senders:
            sender.start()
        assert _await(lambda: slots.in_flight() == DEFAULT_UPLOAD_SLOTS), (
            "the burst never filled the slots"
        )

        asked = time.monotonic()
        metadata = httpx.get(f"{base}/datasets", headers=KEY, timeout=30)
        held = time.monotonic() - asked
        for sender in senders:
            sender.join(30)

        assert metadata.status_code == 200
        assert held < backend.delay * 0.75, f"a dataset listing waited {held:.2f} s on uploads"
        assert 429 in statuses, "a burst deeper than the slots was not refused"
        assert slots.in_flight() == 0, "the burst left a slot behind"
    finally:
        next(served, None)
