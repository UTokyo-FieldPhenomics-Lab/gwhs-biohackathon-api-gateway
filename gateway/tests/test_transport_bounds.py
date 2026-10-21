"""The transport's three clocks, its size ceiling, and a fallback that is not poisoned.

These are the bounds a real backend exchange needs and that an injected ``Transport`` cannot show:
which phase a timeout ended, whether a slow reader or a drip-fed answer can outlive the request's
one deadline, whether an oversized answer is refused without being buffered, and whether a healthy
machine is still tried when the first one cannot be connected to. The rest of the suite substitutes
a transport and therefore never reaches any of this.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

import pytest
from fastapi import HTTPException

from biohack_gateway import models
from biohack_gateway.models import (
    MAX_RESPONSE_BYTES,
    Reply,
    http_transport,
)

MODEL = "sam-test"
TOKEN = "a-backend-token"

DECLARATION = {
    "model": MODEL,
    "resolution": 1008,
    "max_in_flight": 1,
    "parameters": [],
    "computed_by": {"machine": "test-machine"},
}
PREDICTION = {"model": MODEL, "objects": []}


def test_a_truncated_content_length_is_not_accepted_as_an_answer() -> None:
    """A complete JSON prefix is not a complete HTTP response when bytes are still owed."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def answer() -> None:
        """Send valid JSON but close before the announced body has ended."""
        with listener.accept()[0] as peer:
            peer.recv(4096)
            peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{}")

    worker = threading.Thread(target=answer, daemon=True)
    worker.start()
    try:
        reply = http_transport("GET", f"http://127.0.0.1:{port}/", {}, None, 1.0)
    finally:
        listener.close()
        worker.join(2)
    assert reply.status == 0
    assert reply.body == b""
    with models._POOL_LOCK:
        assert not models._IDLE.get(("http", "127.0.0.1", port))


class _Backend(BaseHTTPRequestHandler):
    """A backend that answers a declaration on `/health` and a body on a prediction.

    The body and the status are class attributes so each test can build its own handler class, and
    ``posts`` counts the prediction calls so a test can prove which machine was reached.
    """

    protocol_version = "HTTP/1.1"
    declaration: bytes = b"{}"
    answer: bytes = b"{}"
    answer_status: int = 200
    posts: int = 0

    def do_GET(self) -> None:
        """Answer the health probe, which is what a declaration is read from."""
        self._answer(200, self.declaration)

    def do_POST(self) -> None:
        """Read the request body, count the call, and answer."""
        type(self).posts += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self._answer(self.answer_status, self.answer)

    def _answer(self, status: int, body: bytes) -> None:
        """Send one complete, bounded response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        # A client that stopped reading (a size ceiling or a deadline) is the test, not a fault.
        with contextlib.suppress(OSError):
            self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Keep the request log off the test's stderr."""


class _Drip(BaseHTTPRequestHandler):
    """A backend that answers one byte at a time, slower than the test's budget."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        """Read the request, then drip a response without ever ending it in time."""
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Length", "200")
        self.end_headers()
        try:
            for _ in range(200):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.02)
        except OSError:
            # The client closed at its deadline, which is exactly what this test asks for.
            pass

    def log_message(self, *args: Any) -> None:
        """Keep the request log off the test's stderr."""


class _ChunkedFlood(BaseHTTPRequestHandler):
    """A backend with no ``Content-Length`` whose body exceeds the test's ceiling."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Answer with one large chunk, so only the read loop can stop it."""
        body = b"x" * 4096
        self.send_response(200)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")

    def log_message(self, *args: Any) -> None:
        """Keep the request log off the test's stderr."""


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, int]:
    """Start one handler on an ephemeral loopback port.

    Parameters
    ----------
    handler : type of BaseHTTPRequestHandler
        The class to serve. A subclass per test keeps each call counter its own.

    Returns
    -------
    server : ThreadingHTTPServer
        The running server, to be shut down by the caller.
    port : int
        The port it listens on.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _backend(
    answer: bytes = json.dumps(PREDICTION).encode(),
    status: int = 200,
) -> tuple[ThreadingHTTPServer, int, type[_Backend]]:
    """Start a stub that declares the model and answers one prediction body."""
    handler = type(
        "Stub",
        (_Backend,),
        {
            "declaration": json.dumps(DECLARATION).encode(),
            "answer": answer,
            "answer_status": status,
            "posts": 0,
        },
    )
    server, port = _serve(handler)
    return server, port, handler


def _url(port: int) -> str:
    """Return the base URL of a stub on a loopback port."""
    return f"http://127.0.0.1:{port}"


def _raw_server(respond: Callable[[socket.socket], None]) -> tuple[socket.socket, int]:
    """Start a raw TCP peer that answers one connection with a caller-written script.

    Parameters
    ----------
    respond : callable
        Receives the accepted socket after the request has arrived, and writes whatever the test
        needs. It may raise `OSError` when the client closes first.

    Returns
    -------
    listener : socket.socket
        The listening socket, to be closed by the caller.
    port : int
        The port it listens on.
    """

    def serve(listener: socket.socket) -> None:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        try:
            connection.settimeout(2.0)
            connection.recv(65536)
            respond(connection)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                connection.close()

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    threading.Thread(target=serve, args=(listener,), daemon=True).start()
    return listener, listener.getsockname()[1]


@pytest.fixture(autouse=True)
def _empty_connection_pool() -> Any:
    """Start every test with an empty idle pool, and close whatever it left behind.

    The pool is process-wide and keyed by port, so one test's kept connection could otherwise be
    handed to another and make a close-on-ceiling assertion meaningless.
    """
    with models._POOL_LOCK:
        models._IDLE.clear()
    yield
    with models._POOL_LOCK:
        for connections in models._IDLE.values():
            for connection in connections:
                connection.close()
        models._IDLE.clear()


def test_a_connect_the_clock_ended_is_marked_as_the_connect() -> None:
    """A handshake that never completed never sent anything, so it cannot be blamed on the link.

    The phase is what `forward` acts on: a timeout before the connection exists means the machine is
    unreachable and another one may be tried; only a timeout while a connected socket is sending is
    the link's, and only that one must not be retried.
    """

    def refuse_to_connect(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        raise TimeoutError("the handshake never completed")

    with mock.patch.object(socket, "create_connection", side_effect=refuse_to_connect):
        started = time.monotonic()
        reply = http_transport("GET", "http://192.0.2.1:9/health", {}, None, 5.0)
        elapsed = time.monotonic() - started

    assert reply.status == 0
    assert reply.timed_out is True
    assert reply.stage == "connect"
    assert elapsed < 1.0, "an injected connect timeout should not wait for the whole budget"


def test_a_connect_timeout_falls_back_to_a_healthy_backend() -> None:
    """The audit's request-level outage: a dead first backend must not answer for the model.

    Reproduced through `ModelRegistry.forward` with the real transport and a real second machine,
    because the bug was that `forward` saw ``stage="send"`` where the phase was really connect and
    answered `504` without trying anything else.
    """
    first_server, first_port, first_handler = _backend()
    second_server, second_port, second_handler = _backend()
    real = socket.create_connection

    def reach_second_only(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        if address == ("127.0.0.1", first_port):
            raise TimeoutError("the first machine is off")
        return real(address, *args, **kwargs)

    try:
        registry = models.ModelRegistry({MODEL: [_url(first_port), _url(second_port)]}, TOKEN)
        # Ordering is by measured round trip; pin it so the failed machine is the one tried first.
        registry._backends[MODEL][0].rtt_seconds = 0.01
        registry._backends[MODEL][1].rtt_seconds = 1.0
        # The declaration's own health call left a kept socket to the first machine, and a reused
        # connection never performs a handshake. Drop the pool so this request must connect.
        with models._POOL_LOCK:
            for connections in models._IDLE.values():
                for connection in connections:
                    connection.close()
            models._IDLE.clear()
        with mock.patch.object(socket, "create_connection", side_effect=reach_second_only):
            reply = registry.forward(
                MODEL,
                "predict",
                "",
                b'{"input": {"url": "https://example.test/x.jpg"}}',
                "application/json",
            )
        first, second = registry._backends[MODEL]
        unhealthy = first.unhealthy_until
        second_errors = second.server_errors
        second_in_flight = second.in_flight
        machine_depths = [machine.in_flight for machine in registry.machines()]
        first_posts = first_handler.posts
        second_posts = second_handler.posts
    finally:
        for server in (first_server, second_server):
            server.shutdown()
            server.server_close()

    assert reply.status == 200
    assert reply.body == json.dumps(PREDICTION).encode()
    assert unhealthy > 0.0, "the machine that could not be connected to was not passed over"
    assert second_errors == 0
    assert second_in_flight == 0, "the attempt kept a lane counted against the fallback"
    assert machine_depths == [0], "a released attempt left a machine's depth raised"
    assert (first_posts, second_posts) == (0, 1), "the healthy machine was not the one asked"


def test_a_drip_fed_response_ends_on_the_wall_clock_not_on_inactivity() -> None:
    """A response that never pauses long enough to trip a socket timeout is still over budget.

    Measured before the fix: 15 bytes at one per 0.04 s took 0.56 s against a 0.1 s budget, because
    every byte reset the inactivity clock. The bound is now a deadline set once for the exchange,
    checked between progress reads.
    """
    server, port = _serve(_Drip)
    try:
        started = time.monotonic()
        reply = http_transport("POST", f"{_url(port)}/x", {}, b"", 0.25)
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()

    assert reply.status == 0
    assert reply.timed_out is True
    assert reply.stage == "read"
    assert 0.2 <= elapsed < 2.0, f"the answer outlived its deadline by {elapsed - 0.25:.2f} s"


def test_a_drip_fed_header_ends_on_the_same_wall_clock() -> None:
    """A peer can drip the status line and headers without ever pausing for a socket timeout.

    `read1` only bounds the body. The header lines are read by `http.client`, one `recv` per line,
    so before the watchdog each byte reset the socket's inactivity clock and the request outlived
    its budget. The shutdown at the deadline is what ends this one, without a second HTTP parser.
    """

    def respond(connection: socket.socket) -> None:
        connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
        for _ in range(500):
            connection.sendall(b"a")
            time.sleep(0.01)

    listener, port = _raw_server(respond)
    try:
        started = time.monotonic()
        reply = http_transport("GET", f"{_url(port)}/slow-headers", {}, None, 0.25)
        elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert reply.status == 0
    assert reply.timed_out is True
    assert reply.stage == "read", "a header the clock ended is the answer's phase, not the send's"
    assert elapsed < 2.0, f"a drip-fed header outlived its deadline by {elapsed - 0.25:.2f} s"
    with models._POOL_LOCK:
        assert not models._IDLE.get(("http", "127.0.0.1", port)), (
            "a connection shut down at the deadline was kept in the pool"
        )


def test_a_drip_fed_chunk_header_ends_on_the_same_wall_clock() -> None:
    """Chunk framing happens inside `http.client`, between body reads, and needs the same bound.

    The first chunk is complete, so the body loop sees progress; the next chunk-size line then
    arrives one byte at a time. Only the watchdog covers that read, and the failure must still be
    the answer's phase with nothing left in the pool.
    """

    def respond(connection: socket.socket) -> None:
        connection.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n")
        time.sleep(0.02)
        connection.sendall(b"1")
        for _ in range(500):
            connection.sendall(b"0")
            time.sleep(0.01)

    listener, port = _raw_server(respond)
    try:
        started = time.monotonic()
        reply = http_transport("GET", f"{_url(port)}/slow-chunk", {}, None, 0.3)
        elapsed = time.monotonic() - started
    finally:
        listener.close()

    assert reply.status == 0
    assert reply.body == b""
    assert reply.timed_out is True
    assert reply.stage == "read", "a chunk header the clock ended is the answer's phase"
    assert elapsed < 2.0, f"a drip-fed chunk header outlived its deadline by {elapsed - 0.3:.2f} s"
    with models._POOL_LOCK:
        assert not models._IDLE.get(("http", "127.0.0.1", port)), (
            "a connection shut down at the deadline was kept in the pool"
        )


def test_an_oversized_chunked_answer_is_stopped_by_the_read_loop() -> None:
    """No ``Content-Length`` means only the byte ceiling can stop an endless answer."""
    server, port = _serve(_ChunkedFlood)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(models, "MAX_RESPONSE_BYTES", 512)
            reply = http_transport("GET", f"{_url(port)}/flood", {}, None, 5.0)
    finally:
        server.shutdown()
        server.server_close()

    assert reply.status == 0
    assert reply.body == b""
    assert reply.timed_out is False
    with models._POOL_LOCK:
        assert not models._IDLE.get(("http", "127.0.0.1", port)), (
            "the connection that answered an oversized body was kept in the pool"
        )


def test_a_legitimate_answer_above_four_mebibytes_is_accepted() -> None:
    """The ceiling is a resource policy with headroom, not a tight fit around one sampled answer.

    The largest answer in the 2026-10-19 load run was 26,216 bytes, and a mask larger than anything
    observed is exactly the case this must not refuse. A five-megabyte answer is an order of
    magnitude above any measured answer and still a quarter of the ceiling.
    """
    answer = json.dumps(
        {
            "model": MODEL,
            "resolution_used": 1008,
            "objects": [
                {
                    "bbox": [1.0, 2.0, 30.0, 40.0],
                    "category": "wheat head",
                    "score": 0.5,
                    "rle": {"size": [2509, 592], "counts": "Z" * (5 * 1024 * 1024)},
                }
            ],
        }
    ).encode()
    assert 4 * 1024 * 1024 <= len(answer) < MAX_RESPONSE_BYTES
    server, port, _ = _backend(answer=answer)
    try:
        reply = http_transport("POST", f"{_url(port)}/models/x/predict", {}, b"{}", 10.0)
    finally:
        server.shutdown()
        server.server_close()

    assert reply.status == 200
    assert reply.body == answer, "a legitimate answer was truncated or refused"


def test_an_oversized_answer_falls_back_and_releases_the_machine() -> None:
    """An oversized body is a broken machine, not an answer: pass it over and free its lane."""
    flood = b"x" * 4096
    first_server, first_port, first_handler = _backend(answer=flood)
    second_server, second_port, second_handler = _backend()
    try:
        registry = models.ModelRegistry({MODEL: [_url(first_port), _url(second_port)]}, TOKEN)
        registry._backends[MODEL][0].rtt_seconds = 0.01
        registry._backends[MODEL][1].rtt_seconds = 1.0
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(models, "MAX_RESPONSE_BYTES", 512)
            reply = registry.forward(MODEL, "predict", "", b"{}", "application/json")
        first, second = registry._backends[MODEL]
        first_unhealthy = first.unhealthy_until
        first_in_flight = first.in_flight
        second_in_flight = second.in_flight
        machine_depths = [machine.in_flight for machine in registry.machines()]
        first_posts = first_handler.posts
        second_posts = second_handler.posts
    finally:
        for server in (first_server, second_server):
            server.shutdown()
            server.server_close()

    assert reply.status == 200
    assert first_unhealthy > 0.0
    assert (first_in_flight, second_in_flight) == (0, 0)
    assert machine_depths == [0]
    assert (first_posts, second_posts) == (1, 1), "the fallback was not asked for the request"
    with models._POOL_LOCK:
        assert not models._IDLE.get(("http", "127.0.0.1", first_port))


def test_a_fallback_is_given_only_the_budget_the_first_attempt_left() -> None:
    """Two attempts do not spend two full budgets, and the shortened one is not a machine verdict.

    The first backend gets the whole clock and times out, so it is passed over. The fallback gets
    the remainder — less than a full attempt — and when that runs out the machine must stay in the
    pool: it was judged on another machine's overspend, not on its own work.
    """
    timeouts: list[float] = []

    def transport(method: str, url: str, headers: Any, body: bytes | None, timeout: float) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        timeouts.append(timeout)
        time.sleep(min(0.3, timeout))
        return Reply(status=0, body=b"", timed_out=True, stage="read")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(models, "RESPONSE_TIMEOUT_SECONDS", 0.5)
        registry = models.ModelRegistry(
            {MODEL: ["http://127.0.0.1:1", "http://127.0.0.1:2"]}, TOKEN, transport
        )
        registry._backends[MODEL][0].rtt_seconds = 0.01
        registry._backends[MODEL][1].rtt_seconds = 1.0
        started = time.monotonic()
        with pytest.raises(HTTPException) as raised:
            registry.forward(MODEL, "predict", "", b"{}", "application/json")
        elapsed = time.monotonic() - started
        first, second = registry._backends[MODEL]
        first_unhealthy = first.unhealthy_until
        second_unhealthy = second.unhealthy_until
        first_errors = first.server_errors
        second_errors = second.server_errors

    assert raised.value.status_code == 504
    assert len(timeouts) == 2, "the fallback was not tried at all"
    assert timeouts[1] < timeouts[0] * 0.7, "the fallback got a second full budget"
    assert elapsed < 0.9, f"two attempts spent two budgets ({elapsed:.2f} s)"
    assert first_unhealthy > 0.0, "the full-budget attempt's machine was not passed over"
    assert second_unhealthy == 0.0, "the machine was quarantined for the request's own clock"
    assert (first_errors, second_errors) == (1, 0)


def test_no_backend_is_asked_after_the_shared_budget_is_spent() -> None:
    """When the first attempt uses the whole clock, a second machine is not woken to fail."""
    calls: list[str] = []

    def transport(method: str, url: str, headers: Any, body: bytes | None, timeout: float) -> Reply:
        if "/health" in url:
            return Reply(status=200, body=json.dumps(DECLARATION).encode())
        calls.append(url)
        time.sleep(timeout)
        return Reply(status=0, body=b"", timed_out=True, stage="read")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(models, "RESPONSE_TIMEOUT_SECONDS", 0.3)
        registry = models.ModelRegistry(
            {MODEL: ["http://127.0.0.1:1", "http://127.0.0.1:2"]}, TOKEN, transport
        )
        registry._backends[MODEL][0].rtt_seconds = 0.01
        registry._backends[MODEL][1].rtt_seconds = 1.0
        started = time.monotonic()
        with pytest.raises(HTTPException) as raised:
            registry.forward(MODEL, "predict", "", b"{}", "application/json")
        elapsed = time.monotonic() - started

    assert raised.value.status_code == 504
    assert len(calls) == 1, "a machine was asked with no budget left to answer"
    assert elapsed < 0.7
