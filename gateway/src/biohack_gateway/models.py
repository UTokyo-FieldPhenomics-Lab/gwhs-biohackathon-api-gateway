"""The model surface: the pool's models answered over HTTP, and nothing else.

The gateway does exactly three things with a model request. It resolves the address a client named
into the public URL of an image, through the same index the dataset surface reads, so both surfaces
address a row identically. It forwards the request to a backend over one uniform protocol. And it
returns the backend's own answer, whose geometry the backend has already put into the shape this
contract publishes.

Nothing here encodes geometry, holds a weight, or knows what a mask is. :class:`ObjectOut` and
:class:`RleMask` are imported from the dataset surface rather than written again: a second
definition of the same body is the one thing this surface must not grow, and `gateway.md` asks for
exactly one object shape across the two surfaces so a participant writes one decoder.

Routing is the whole algorithm — try a model's backends in order, idle ones first, mark a failed one
unhealthy for at least as long as the failure cost, move on. There is no circuit breaker and no
weighted choice, which are for a pool larger than this one, and no health loop, which is what
`gateway.md` forbids: the gateway learns a backend is gone from a real request's failure, and
:data:`RESPONSE_TIMEOUT_SECONDS` is what that costs.

**Control parameters come from the model, not from here.** A model declares what it accepts — the
prompt it can be given, the threshold it applies — and the gateway repeats that declaration in
``GET /models`` and forwards the values verbatim. It is the one place the parameters appear, so a
new model needs no gateway change, the same bargain the dataset surface strikes with a published
index.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from biohack_gateway.dataset_index import Dataset
from biohack_gateway.datasets import ObjectOut, image_addresses

logger = logging.getLogger(__name__)

DEFAULT_MODELS_FILE = Path.home() / ".config" / "biohack-gateway" / "models.json"
"""Where the host names its models and their backends, as ``{"<model>": ["<url>", ...]}``.

Host configuration beside the token pool, for the reason the runbook gives: it names running
machines, so it does not belong in this repository. The file need not exist — a gateway with no
models configured answers an empty directory rather than refusing to start.
"""

UNHEALTHY_SECONDS = 30.0
"""How long a backend that failed a request is passed over.

The number `gateway.md` fixes. It is long enough for a container to be restarted and short enough
that a recovered backend is not shunned for the rest of an event.

It is also the floor of an invariant, not a free number: it must be **at least**
:data:`RESPONSE_TIMEOUT_SECONDS`, or a failure is retried before the wait it cost has
passed, and the same minute is paid again. The assertion below is the invariant; the
measurements behind it are in `deploy/gpu-backends.md`.
"""

DECLARATION_TIMEOUT_SECONDS = 3.0
"""How long a backend is given to answer what it serves, at startup only.

Short on purpose: this is the one moment the gateway waits on a machine that may be switched off,
and a model whose backend is off appears in the directory with nothing declared rather than holding
up the process.
"""

CONNECT_TIMEOUT_SECONDS = 3.0
"""How long one attempt may spend getting a socket to a backend.

Separate from the response budget because the two failures are different facts. A machine that
is off or rebooting drops the SYN and nothing answers, so only the clock ends the attempt,
while a backend that is answering needs the whole budget for the work it is doing. Measured
2026-10-16: a *stopped* container on the laboratory 5090 does not accept the connection either
— an attempt against it ended here, at 3 s, where the same attempt had run the full 60-second
response budget before this bound existed. What the response budget covers instead is a
backend that accepts and then goes quiet, which none of these machines does.
"""

RESPONSE_TIMEOUT_SECONDS = 10.0
"""How long one inference may take before the backend is treated as failed.

**One number, and the only budget a request has.** Measured 2026-10-16 on the laboratory
5090 through this gateway: 0.9–1.0 s for a published row, and 0.3 s at the backend for an
18-megapixel upload whose mask came back 2.1 megapixels wide — the encoder runs at a fixed
1008 side, so the input's size barely moves the figure. Ten seconds is therefore an order
of magnitude of headroom, and it is the *ceiling* on what a dead backend costs: a backend
that never accepts the connection costs :data:`CONNECT_TIMEOUT_SECONDS` instead, which is
what every dead backend here has actually done.
"""

assert UNHEALTHY_SECONDS >= RESPONSE_TIMEOUT_SECONDS, (
    "a failed backend must be passed over for at least as long as the failure it remembers cost"
)

DECLARATION_RETRY_SECONDS = 30.0
"""How long before a backend that did not answer at all is asked again.

Not a poll: a backend that has declared something is never re-read until the gateway restarts, and
a machine that is switched off is asked once per window rather than once per request. It exists
because the gateway and a container start independently and either can restart alone — a container
that was still loading its weights when the gateway came up would otherwise stay undescribed until
somebody restarted the *gateway*, which is the one thing nobody would think to do.
"""

DECLARATION_LOADING_RETRY_SECONDS = 3.0
"""How long before a backend that answered "still loading" is asked again.

A shorter wait than a silent machine gets, because the two are different facts: ``503`` means a
container that is warming up and will describe itself in seconds, while no answer means a machine
that may be off for the afternoon. Without the distinction, a gateway started before its container
would show an empty directory for half a minute after the container was up, which is exactly the
window somebody checks it in.
"""

PROBE_INTERVAL_SECONDS = 15.0
"""How often every backend is asked whether it is still answering.

The published count is a probe's verdict rather than a record of the last failure, so it needs a
clock of its own. Without one, two lies are possible and both were observed: a machine that dies
quietly stays published as up until somebody calls it, and a machine that was off when the process
started is published as up because nothing has failed against it yet.

Fifteen seconds because it is short enough that a reader who starts a container and presses Run
sees it, and long enough that the pool costs one request per machine per quarter minute. What it
must not become is a probe per request, or a probe that re-reads a declaration: those are the two
things `gateway.md` rules out, and the difference between this and monitoring is that nothing here
is measured into a time series — it is one verdict, replaced by the next one.
"""


class ModelParameter(BaseModel):
    """One control parameter a model accepts, as the model declares it.

    Attributes
    ----------
    name : str
        The parameter's name, which is the query parameter's name on the predict routes.
    default : str or float or None
        What the model uses when the client sends nothing, where it has a default.
    minimum, maximum : float or None
        The accepted range, where the parameter is numeric and bounded.
    description : str
        One line saying what the parameter does, safe to publish.
    """

    name: str
    default: str | float | None = None
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""


class ModelSummary(BaseModel):
    """One model, as both the directory and the detail route publish it.

    The detail route answers the same object as the directory holds, which is what makes an id
    quotable: a client fetches one model rather than filtering the directory, and the two can never
    disagree.

    Attributes
    ----------
    id : str
        The model id a client addresses, and the first segment of every one of its routes.
    resolution : int or None
        The square side the model runs at, as it declared. `None` where nothing answered.
    backends_up : int
        How many of its backends answered the last probe and are not passed over. Zero means a call
        will be refused rather than queued — the published count and the routing decision are taken
        from the same set.
    max_in_flight : int
        Requests the answering backends declare they can hold at once, summed. Published rather than
        enforced: the concurrency gate is not implemented yet.
    parameters : list of ModelParameter
        What the model accepts. Empty where no backend has declared anything.
    machines : list of ComputedBy
        One record per answering backend that declared itself — the machine's name, its card, the
        capability and the stack. A reader learns which machines would answer **before** calling,
        which is what makes a difference between two answers explainable rather than surprising. It
        is not an inventory: an address, a memory figure or a queue depth has no place here.
    checked_seconds_ago : int or None
        How long ago the pool was last asked, to the nearest second. It is part of the count rather
        than decoration: the number is a probe's verdict, and a verdict without its age is a promise
        about the past. `None` where this model has no backends to ask.
    """

    id: str
    resolution: int | None = None
    backends_up: int = 0
    max_in_flight: int = 0
    parameters: list[ModelParameter] = field(default_factory=list)
    machines: list[ComputedBy] = field(default_factory=list)
    checked_seconds_ago: int | None = None


class ModelDirectory(BaseModel):
    """The `GET /models` body.

    Attributes
    ----------
    models : list of ModelSummary
        One entry per configured model, ordered by id.
    """

    models: list[ModelSummary]


class ComputedBy(BaseModel):
    """The machine that produced an answer, as that machine reported itself.

    A mask's boundary is the output of kernels chosen at run time, so two machines can disagree by a
    pixel, by a percent of a mask's area, or by one object at the margin. Publishing this turns such
    a difference from a mystery into a fact a caller can read. It is a reproducibility record rather
    than an inventory: no address, no memory figure, no hostname.

    Every field is optional, which is what lets a backend that has not been rebuilt still answer: a
    missing record is unhelpful, never wrong.

    Attributes
    ----------
    machine : str or None
        The name the machine answers under — the operator's label where one is set, the host name
        otherwise. It is what still identifies a host when every hardware field is null.
    gpu : str or None
        The card's name, where the backend has one.
    compute_capability : str or None
        The card's capability as `major.minor` — the kernel family that ran.
    cuda : str or None
        The CUDA version the build was made against.
    torch : str or None
        The torch version that ran the forward pass.
    """

    machine: str | None = None
    gpu: str | None = None
    compute_capability: str | None = None
    cuda: str | None = None
    torch: str | None = None


class ModelPrediction(BaseModel):
    """The answer to a predict request, whatever the model was.

    Attributes
    ----------
    model : str
        The model that answered.
    objects : list of ObjectOut
        The same object shape the dataset surface returns, so one decoder reads both.
    resolution_used : int or None
        The side the model actually ran at, where it reports one.
    computed_by : ComputedBy or None
        The machine that produced the answer, where the backend reports one.
    """

    model: str
    objects: list[ObjectOut]
    resolution_used: int | None = None
    computed_by: ComputedBy | None = None


@dataclass(frozen=True)
class Reply:
    """One backend's answer, reduced to what the gateway acts on.

    Attributes
    ----------
    status : int
        The HTTP status the backend answered with, or 0 when the connection failed.
    body : bytes
        The response body, empty when there is none.
    timed_out : bool
        Whether the failure was the clock rather than the backend, which decides the error code the
        caller sees.
    """

    status: int
    body: bytes
    timed_out: bool = False


Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], Reply]
"""How the gateway reaches a backend: ``(method, url, headers, body, timeout) -> Reply``.

A function rather than an HTTP client, so a test can answer for a backend without starting one, and
so this package keeps depending on nothing but the standard library.
"""


class _BoundedHTTPConnection(http.client.HTTPConnection):
    """A plain HTTP connection whose connect phase has its own, much shorter, budget.

    ``HTTPConnection.connect`` hands the *response* budget to ``socket.create_connection``,
    and that is what makes a machine which is switched off cost a whole inference of
    waiting: the SYN is dropped, nothing answers, and only the clock can end the attempt.
    Bounding the handshake here limits that failure to :data:`CONNECT_TIMEOUT_SECONDS`; the
    socket then takes the response budget back, which is what the backend needs for the
    work it is actually doing.
    """

    def connect(self) -> None:
        """Open the socket under the connect budget, then restore the response budget.

        ``TCP_NODELAY`` is set here because this method replaces ``HTTPConnection.connect``, which
        sets it, and a socket without it pays a delayed acknowledgement on the write that follows
        another one — a request's headers and body go as two writes, so every call waited for the
        peer's ACK before the body left. Measured on loopback 2026-10-17: 81 ms per call against
        41 ms for a socket the standard library had configured, and on the 260 ms link to the
        laboratory backends the same delay is bounded by the round trip instead of by 40 ms.
        """
        self.sock = socket.create_connection((self.host, self.port), CONNECT_TIMEOUT_SECONDS)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:  # an OS without it: the connect budget still matters more than the delay
            logger.debug("TCP_NODELAY is not available on this platform")
        self.sock.settimeout(self.timeout)


# An `https` backend keeps the plain class. Bounding a TLS handshake the same way would mean
# duplicating the wrapping `HTTPSConnection.connect` does, and no backend is https: they sit on a
# private network and `models.json` names them `http://`. If one ever is, revisit this line.
_CONNECTIONS: dict[str, type[http.client.HTTPConnection]] = {
    "http": _BoundedHTTPConnection,
    "https": http.client.HTTPSConnection,
}

POOL_IDLE_PER_BACKEND = 4
"""Idle connections kept per backend, which is the concurrency one machine is asked for anyway.

More than a handful would sit unused: the pool is one backend per machine, each with a serial lane,
and the gateway's own thread pool is what bounds simultaneous calls. Four is enough that a burst
finds a warm socket without holding a descriptor open per thread.
"""

_IDLE: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
_POOL_LOCK = threading.Lock()


def _checkout(key: tuple[str, str, int]) -> http.client.HTTPConnection | None:
    """Take an idle connection to one backend, if there is one.

    Parameters
    ----------
    key : tuple of (str, str, int)
        Scheme, host, and port — the identity of the machine a connection is to.

    Returns
    -------
    http.client.HTTPConnection or None
        A connection another request has finished with, or `None` when there is none to take. The
        object is handed to exactly one caller, because a connection carries a response stream and
        cannot be shared by two requests at once.
    """
    with _POOL_LOCK:
        idle = _IDLE.get(key)
        return idle.pop() if idle else None


def _checkin(key: tuple[str, str, int], connection: http.client.HTTPConnection) -> None:
    """Return a connection that answered completely and is still open.

    Parameters
    ----------
    key : tuple of (str, str, int)
        The backend it belongs to.
    connection : http.client.HTTPConnection
        The connection, whose response has been read to the end.

    Notes
    -----
    The pool is capped rather than unbounded: an idle socket still costs a descriptor, and a burst
    that opens forty at once should not leave forty of them open for the rest of the process.
    """
    with _POOL_LOCK:
        idle = _IDLE.setdefault(key, [])
        if len(idle) >= POOL_IDLE_PER_BACKEND:
            connection.close()
        else:
            idle.append(connection)


def http_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> Reply:
    """Send one request to a backend over HTTP, and reduce the outcome to a :class:`Reply`.

    Parameters
    ----------
    method : str
        The HTTP method.
    url : str
        The absolute backend URL.
    headers : mapping of str to str
        Headers to send.
    body : bytes or None
        The request body, where there is one.
    timeout : float
        Seconds to wait for an answer, which is :data:`RESPONSE_TIMEOUT_SECONDS` in practice. The
        connect phase is bounded separately and more tightly, by :data:`CONNECT_TIMEOUT_SECONDS`.

    Returns
    -------
    Reply
        The status and body, or a failure described as one. A refused connection and a timeout are
        both failures rather than exceptions here: every caller's next step is the same, and the
        difference only decides which code the participant sees.

    Notes
    -----
    ``http.client`` rather than ``urllib``, for two reasons. The connect phase needs a budget of its
    own, which ``urllib`` has no seam for. And a redirect is deliberately **not** followed: the
    backend is ours and answers where it is asked, so a `3xx` is returned as the answer it is rather
    than turning this gateway into a client that fetches whatever a named backend points it at.

    A connection that answered completely is kept for the next call to the same backend, because the
    machines are hundreds of milliseconds away and a handshake per request was most of a call's
    latency when a participant asked for one image at a time (measured 2026-10-17). A pooled
    connection the peer has already closed is **retried once on a fresh one**: a keep-alive window
    running out is not a failed backend, and without that retry pooling would turn a saving into the
    first request after every quiet period failing.
    """
    parsed = urllib.parse.urlsplit(url)
    factory = _CONNECTIONS.get(parsed.scheme)
    if factory is None or not parsed.hostname:
        logger.warning("backend url is not an http(s) address: %s", url)
        return Reply(status=0, body=b"")

    key = (parsed.scheme, parsed.hostname, parsed.port or 0)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    for attempt in (0, 1):
        connection = _checkout(key) if attempt == 0 else None
        reused = connection is not None
        if connection is None:
            connection = factory(parsed.hostname, parsed.port, timeout=timeout)
        connection.timeout = timeout
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            try:
                payload = response.read()
            finally:
                response.close()
        except TimeoutError:
            connection.close()
            return Reply(status=0, body=b"", timed_out=True)
        except ConnectionError as error:
            # `RemoteDisconnected` is a `ConnectionResetError`, and a reset or a broken pipe here is
            # the peer having closed a connection we were keeping. Retried once, never twice, and
            # only when the connection came from the pool: one made for this attempt failing is a
            # backend that is down, not a window that ran out.
            connection.close()
            if reused:
                logger.info("a kept connection to %s had closed; asking again", parsed.hostname)
                continue
            logger.warning("backend unreachable: %s (%s)", url, error)
            return Reply(status=0, body=b"")
        except (OSError, http.client.HTTPException) as error:
            # Connection refused, no route, DNS, a bad status line, a truncated answer — one outcome
            # for all of them. `socket.timeout` is an OSError subclass on some platforms and a
            # TimeoutError on others; the type is checked in that order by the clauses above.
            connection.close()
            logger.warning("backend unreachable: %s (%s)", url, error)
            return Reply(status=0, body=b"")
        if response.will_close:
            connection.close()
        else:
            _checkin(key, connection)
        return Reply(status=response.status, body=payload)
    return Reply(status=0, body=b"")


def load_models(path: Path) -> dict[str, list[str]]:
    """Read the host's model-to-backend list.

    Parameters
    ----------
    path : Path
        The file. It need not exist.

    Returns
    -------
    dict of str to list of str
        One entry per model, with its backend URLs in the order they are to be tried. Empty when the
        file is absent, unreadable, or does not parse.

    Notes
    -----
    A file that does not parse yields **no models**, never all of them: the surface fails closed the
    same way the request gate does, because a typo in host configuration must not become a gateway
    that answers for models nobody configured. The reason is logged once, here, rather than on every
    request that finds nothing.
    """
    if not path.exists():
        logger.info("no model-backend list at %s; the model surface has no models", path)
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.error("model-backend list at %s does not parse (%s); serving no models", path, error)
        return {}
    if not isinstance(document, dict):
        logger.error("model-backend list at %s must be an object of model to URL list", path)
        return {}

    models: dict[str, list[str]] = {}
    for model, urls in document.items():
        if not isinstance(urls, list) or not all(isinstance(url, str) and url for url in urls):
            logger.error("model %r in %s must map to a list of non-empty URLs", model, path)
            continue
        models[str(model)] = [url.rstrip("/") for url in urls]
    return models


@dataclass
class Backend:
    """One machine that can serve a model, and what it said about itself.

    Attributes
    ----------
    url : str
        The base URL, without a trailing slash.
    resolution : int or None
        The side it declared it runs at.
    max_in_flight : int
        How many requests it declared it can hold at once.
    parameters : list of ModelParameter
        What it declared it accepts.
    unhealthy_until : float
        A monotonic deadline: while now is below it, this backend is passed over.
    alive : bool
        Whether the last probe said it is answering. This, and not "has not failed yet", is what
        ``backends_up`` counts.
    probed_at : float
        When it was last asked, as a monotonic stamp, so the published count can carry its age. Zero
        means it has never been asked.
    in_flight : int
        Attempts this gateway currently has running against this backend. Read when choosing which
        backend to try, so that a request arriving while another is in flight to one that is in fact
        dead does not queue behind that backend's own timeout.
    """

    url: str
    resolution: int | None = None
    max_in_flight: int = 1
    parameters: list[ModelParameter] = field(default_factory=list)
    machine: ComputedBy | None = None
    """What the backend said it is, where it said anything. An undeclared backend has none."""
    unhealthy_until: float = 0.0
    alive: bool = False
    probed_at: float = 0.0
    in_flight: int = 0
    declared: bool = False
    """Whether this backend has answered what it serves, ever. Once true it is never asked again."""
    answered: bool = False
    """Whether the last declaration attempt got any answer, which decides how long to wait next."""
    tried_at: float = 0.0
    """When the declaration was last attempted, so a refusal is not repeated per request."""

    def is_passed_over(self, now: float) -> bool:
        """Say whether this backend is still in its thirty seconds of quiet.

        Parameters
        ----------
        now : float
            The current monotonic clock.

        Returns
        -------
        bool
            True while the backend should not be tried.
        """
        return now < self.unhealthy_until


class ModelRegistry:
    """The configured models, what their backends declared, and which ones are answering.

    The registry is built once, at startup, and lives for the process. **A declaration is read once
    and never re-read**: what a model serves is a specification, and a specification that changes
    under a running process is a deployment that has not been restarted. **Availability is the other
    thing**, and it is probed — :func:`watch_pool`, every :data:`PROBE_INTERVAL_SECONDS` — because a
    count that only moves when a request fails is a count that lies about a machine which died
    quietly, and about one that was off before the process started.

    The dataset index still strikes the older bargain, reading its files once, because a published
    dataset does not go away while the gateway serves; only the pool has a clock.
    """

    def __init__(
        self,
        models: Mapping[str, Sequence[str]],
        backend_token: str,
        transport: Transport = http_transport,
    ) -> None:
        """Build the registry and ask every backend what it serves.

        Parameters
        ----------
        models : mapping of str to sequence of str
            The host's model-to-backend list.
        backend_token : str
            The token presented to backends as `X-Backend-Token`. Empty means no backend can be
            called, which is a misconfiguration the predict routes report rather than hide.
        transport : Transport, optional
            How to reach a backend. Injectable so a test can answer for one.
        """
        self._token = backend_token
        self._transport = transport
        # Guards `Backend.in_flight` only, and is held for an increment and a decrement. The sync
        # predict routes run in FastAPI's thread pool, so two requests really do touch one backend's
        # counter at the same time.
        self._lock = threading.Lock()
        self._backends: dict[str, list[Backend]] = {
            model: [Backend(url=url) for url in urls] for model, urls in models.items()
        }
        for model, backends in self._backends.items():
            for backend in backends:
                self._declare(model, backend)

    def _ask(self, backend: Backend) -> Reply:
        """Ask one backend what it is now, and record that it was asked.

        Parameters
        ----------
        backend : Backend
            The backend to ask. `alive`, `answered` and `probed_at` are updated from the answer.

        Returns
        -------
        Reply
            The answer, for a caller that also wants the declaration out of it.

        Notes
        -----
        A `200` is the only answer that counts as alive: a container still loading its weights
        answers `503`, and publishing it as up would promise a call it would refuse.
        """
        reply = self._transport(
            "GET", f"{backend.url}/health", {}, None, DECLARATION_TIMEOUT_SECONDS
        )
        backend.alive = reply.status == 200
        backend.answered = reply.status != 0
        backend.probed_at = time.monotonic()
        return reply

    def _declare(self, model: str, backend: Backend) -> None:
        """Ask one backend what it serves, and record the answer.

        Parameters
        ----------
        model : str
            The model the backend is listed under.
        backend : Backend
            The backend to ask. It is mutated with whatever it answers.

        Notes
        -----
        A backend answers this without a token, because ``/health`` is for a human and the deploy
        script and carries no data. A backend that does not answer is logged and left undeclared:
        its address still routes, and a later request asks it again, so a container that was still
        loading its weights when the gateway started describes itself once it is up.
        """
        backend.tried_at = time.monotonic()
        reply = self._ask(backend)
        if reply.status != 200:
            logger.warning("backend %s did not declare itself (%s)", backend.url, reply.status)
            return
        try:
            declaration = json.loads(reply.body)
        except json.JSONDecodeError:
            logger.warning("backend %s answered an unreadable declaration", backend.url)
            return
        if not isinstance(declaration, dict):
            logger.warning("backend %s answered a declaration that is not an object", backend.url)
            return
        if declaration.get("model") != model:
            # The list is what the gateway was told to route by; the declaration says what is
            # actually there. A mismatch is someone's edit not having landed, and it is worth saying
            # out loud rather than silently routing to the wrong model.
            logger.warning(
                "backend %s declares model %r but is listed under %r",
                backend.url,
                declaration.get("model"),
                model,
            )
        backend.resolution = declaration.get("resolution")
        if isinstance(declaration.get("max_in_flight"), int):
            backend.max_in_flight = declaration["max_in_flight"]
        backend.parameters = _declared_parameters(declaration.get("parameters"))
        backend.machine = _declared_machine(declaration.get("computed_by"))
        backend.declared = True

    def _refresh_undeclared(self, model: str, backends: Sequence[Backend]) -> None:
        """Ask any backend that has never declared itself, at most once per retry window.

        Parameters
        ----------
        model : str
            The model the backends are listed under.
        backends : sequence of Backend
            The backends whose declarations may be stale or missing.

        Notes
        -----
        This is what keeps a gateway started before its containers from describing nothing forever.
        It is bounded twice over: only undeclared backends are asked, and each is asked at most once
        per window — :data:`DECLARATION_LOADING_RETRY_SECONDS` where the machine answered and said
        it was still loading, and :data:`DECLARATION_RETRY_SECONDS` where it did not answer at all.
        A backend that has declared something is never re-read, so the steady state costs no
        request.
        """
        now = time.monotonic()
        for backend in backends:
            if backend.declared:
                continue
            window = (
                DECLARATION_LOADING_RETRY_SECONDS if backend.answered else DECLARATION_RETRY_SECONDS
            )
            if now - backend.tried_at < window:
                continue
            self._declare(model, backend)

    def probe(self) -> None:
        """Ask every backend whether it is still answering, and say so when that changes.

        Notes
        -----
        Nothing is re-read: a declaration is what a model serves, and this asks only whether the
        machine is there, so a spec cannot change underneath a page that is being used. A backend
        that answers is logged when it starts answering and when it stops — never on each pass,
        because a machine that is off for an afternoon would otherwise write a line every
        :data:`PROBE_INTERVAL_SECONDS`.
        """
        for backends in self._backends.values():
            for backend in backends:
                self._probe(backend)

    def _probe(self, backend: Backend) -> None:
        """Ask one backend, and log the answer only where it differs from the last one."""
        before = backend.alive
        self._ask(backend)
        if backend.alive == before:
            return
        if backend.alive:
            logger.info("backend %s is answering", backend.url)
        else:
            logger.warning("backend %s stopped answering", backend.url)

    def directory(self) -> list[ModelSummary]:
        """Describe every configured model.

        Returns
        -------
        list of ModelSummary
            One entry per model, in id order.
        """
        return [self.describe(model) for model in sorted(self._backends)]

    def describe(self, model: str) -> ModelSummary:
        """Describe one model, or refuse.

        Parameters
        ----------
        model : str
            The model id from the path.

        Returns
        -------
        ModelSummary
            The model's id, what its backends declared, and how many are answering.

        Raises
        ------
        HTTPException
            No model has that id. The name is not repeated in the message, so nothing a caller sent
            is echoed back.
        """
        backends = self._backends.get(model)
        if backends is None:
            raise HTTPException(status_code=404, detail="no such model")
        self._refresh_undeclared(model, backends)
        now = time.monotonic()
        live = [
            backend for backend in backends if backend.alive and not backend.is_passed_over(now)
        ]
        declared = next((backend for backend in backends if backend.declared), None)
        checked = max((backend.probed_at for backend in backends), default=0.0)
        return ModelSummary(
            id=model,
            resolution=declared.resolution if declared else None,
            backends_up=len(live),
            max_in_flight=sum(backend.max_in_flight for backend in live),
            parameters=declared.parameters if declared else [],
            machines=[backend.machine for backend in live if backend.machine is not None],
            # The age of the newest look, not the oldest: the probe asks a model's backends in one
            # pass, so what a reader needs to know is when that pass happened.
            checked_seconds_ago=None if not checked else max(0, round(now - checked)),
        )

    def pool_width(self) -> int:
        """Report the widest a model's pool declares it can hold at once.

        Returns
        -------
        int
            The largest per-model sum of `max_in_flight` over the backends that are answering, at
            least `1`. It is the number `GET /models` publishes, read as a capacity rather than as a
            description, so the per-token cap that uses it cannot disagree with what a client is
            told. One is the floor because a gateway with no backend live still authenticates and
            must not refuse every request while it has nothing to route to — those requests answer
            `503` from the model surface, which is the honest refusal.

        Notes
        -----
        Taken under the same lock as routing, so a pool that is being re-probed cannot report a
        width that never existed.
        """
        now = time.monotonic()
        with self._lock:
            widths = [
                sum(
                    backend.max_in_flight
                    for backend in backends
                    if backend.alive and not backend.is_passed_over(now)
                )
                for backends in self._backends.values()
            ]
        return max(widths, default=1) or 1

    def _begin(self, model: str) -> list[Backend]:
        """Order a model's live backends and reserve the one that will be tried first.

        Parameters
        ----------
        model : str
            The model id.

        Returns
        -------
        list of Backend
            The backends that are answering and not currently passed over, with those that have
            nothing in flight first, and the first of them already counted as in flight. Empty
            means the caller should be told the model is unavailable rather than made to wait for
            a machine that is off — and it is the same set the published count is taken from, so
            `0` means the same thing in both places.

        Raises
        ------
        HTTPException
            No model has that id.

        Notes
        -----
        Ordering and reserving happen in one critical section on purpose. A request that read the
        order and only then counted itself would still choose a backend another request had just
        taken, which is exactly the case the preference exists for: a backend is only known to be
        unhealthy *after* an attempt against it fails, so a burst arriving during the failure would
        all pick it. Measured on 2026-10-16 with the laboratory 5090 stopped and three requests sent
        at once: two of the three still queued behind the failure until this was made atomic.
        """
        if model not in self._backends:
            raise HTTPException(status_code=404, detail="no such model")
        with self._lock:
            backends = self._order(self._backends[model])
            if backends:
                backends[0].in_flight += 1
        return backends

    def _order(self, backends: Sequence[Backend]) -> list[Backend]:
        """Return the live backends with those that have nothing in flight first, then the rest.

        Parameters
        ----------
        backends : sequence of Backend
            One model's backends, in the order the host listed them.

        Returns
        -------
        list of Backend
            The same backends, with an idle one first and the busy ones in ascending order of what
            they already hold, so a burst deeper than the pool joins the shortest queue. Sorting is
            stable, so backends holding the same number of requests keep list order.

        Notes
        -----
        The busy group used to keep list order, which made the first entry the pool's only lane:
        measured against three cards on 2026-10-17, 160 simultaneous calls put 140 on the first
        listed backend and 20 on the other two, and the pool answered at one card's rate. Idle-first
        alone does not cover that case, because a backend is only known to be busy after a request
        has already been counted against it.
        """
        now = time.monotonic()
        live = [
            backend for backend in backends if backend.alive and not backend.is_passed_over(now)
        ]
        idle = [backend for backend in live if not backend.in_flight]
        busy = sorted((backend for backend in live if backend.in_flight), key=lambda b: b.in_flight)
        return idle + busy

    def _reserve(self, backend: Backend) -> None:
        """Count one attempt against a backend, a fallback reached later in the same request."""
        with self._lock:
            backend.in_flight += 1

    def _release(self, backend: Backend) -> None:
        """Stop counting one attempt.

        Always called from a `finally`: an exception on this path must not leave a backend looking
        permanently busy, which would take it out of the pool for the life of the process.
        """
        with self._lock:
            backend.in_flight -= 1

    def forward(
        self,
        model: str,
        path: str,
        query: str,
        body: bytes | None,
        content_type: str,
    ) -> Reply:
        """Send one request to the first backend of a model that answers.

        Parameters
        ----------
        model : str
            The model id.
        path : str
            The path below the model, without a leading slash — `predict` or `predict/upload`.
        query : str
            The query string as the client sent it, forwarded verbatim: the parameters are the
            model's, and the gateway does not know their names.
        body : bytes or None
            The request body, already resolved into what a backend expects.
        content_type : str
            The content type to send with it.

        Returns
        -------
        Reply
            The first backend's answer. A backend that failed is marked unhealthy for
            :data:`UNHEALTHY_SECONDS` and the next one is tried.

        Raises
        ------
        HTTPException
            `503` when no backend may be tried — all of them are passed over, or the host
            configured none for this model; or when every backend that was tried failed, reported as
            an upstream failure with the status the last one gave.
        """
        backends = self._begin(model)
        if not self._token:
            # Fail closed and say whose fault it is. Without a token every backend would answer 401,
            # which the surface maps to a client error — a misconfigured gateway reported as a bad
            # request, which sends the reader looking in the wrong place.
            raise HTTPException(
                status_code=503,
                detail="the gateway is not configured to reach models",
            )
        if not backends:
            raise HTTPException(
                status_code=503,
                detail="no backend is answering for this model right now",
            )

        headers = {
            "X-Backend-Token": self._token,
            "Content-Type": content_type,
            "Accept": "application/json",
        }
        suffix = f"?{query}" if query else ""
        failures: list[Reply] = []
        for index, backend in enumerate(backends):
            if index:
                # A fallback was not reserved with the list, because it was not being tried yet.
                self._reserve(backend)
            url = f"{backend.url}/models/{model}/{path}{suffix}"
            try:
                reply = self._transport("POST", url, headers, body, RESPONSE_TIMEOUT_SECONDS)
            finally:
                self._release(backend)
            if 200 <= reply.status < 300:
                backend.unhealthy_until = 0.0
                return reply
            if 400 <= reply.status < 500:
                # The request's fault, so it is the client's answer and no other backend is asked:
                # an identical request would fail identically everywhere.
                if reply.status in (401, 403):
                    # Except this one, which is never the caller's fault: the gateway's own token
                    # was refused, and telling a participant their key is bad would send them to the
                    # wrong place with a key that is fine.
                    logger.error("a backend refused the gateway's token (%s)", reply.status)
                    raise HTTPException(
                        status_code=503,
                        detail="the gateway cannot authenticate to a backend right now",
                    )
                raise HTTPException(status_code=reply.status, detail=_refusal(reply))
            # The cause is named rather than reduced to a status: `0` covers a refused connection,
            # no route and an unreadable name, and none of them is a timeout — a log line reading
            # "timeout" would send an operator to the wrong knob. The transport logs the operating
            # system's own words one line above.
            cause = (
                "timed out"
                if reply.timed_out
                else f"status {reply.status}"
                if reply.status
                else "unreachable"
            )
            logger.warning("backend %s failed a %s: %s", backend.url, model, cause)
            backend.unhealthy_until = time.monotonic() + UNHEALTHY_SECONDS
            failures.append(reply)

        last = failures[-1]
        if last.timed_out:
            raise HTTPException(status_code=504, detail="the model took too long to answer")
        raise HTTPException(
            status_code=502,
            detail=f"every backend for this model failed with {last.status}",
        )


def _declared_parameters(raw: Any) -> list[ModelParameter]:
    """Read a backend's declared parameter list, ignoring anything malformed.

    Parameters
    ----------
    raw : Any
        Whatever the backend put in its declaration's `parameters` field.

    Returns
    -------
    list of ModelParameter
        The parameters that parsed. A malformed entry is dropped rather than failing the whole
        declaration, because a model that is otherwise answering should still be listed.
    """
    if not isinstance(raw, list):
        return []
    parameters: list[ModelParameter] = []
    for entry in raw:
        try:
            parameters.append(ModelParameter.model_validate(entry))
        except ValueError:
            logger.warning("ignoring an unreadable parameter declaration: %r", entry)
    return parameters


def _declared_machine(raw: Any) -> ComputedBy | None:
    """Read a backend's self-report, ignoring anything that is not one.

    Parameters
    ----------
    raw : Any
        Whatever the backend put in its declaration's `computed_by` field.

    Returns
    -------
    ComputedBy or None
        The record, or `None` where the backend said nothing or said something unreadable — so a
        backend that has not been rebuilt contributes nothing rather than failing the whole model.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return ComputedBy.model_validate(raw)
    except ValueError:
        logger.warning("ignoring an unreadable machine record: %r", raw)
        return None


def _image_url(datasets: Mapping[str, Dataset], dataset: str, row: int, base: str) -> str:
    """Resolve an address to the public URL of its image, or refuse.

    Parameters
    ----------
    datasets : mapping of str to Dataset
        The index the dataset surface reads, so both surfaces address a row identically.
    dataset : str
        The dataset id, a lookup key in the closed index.
    row : int
        The absolute row of the pinned revision.
    base : str
        The distribution's base URL, without a trailing slash.

    Returns
    -------
    str
        The public `data.*` URL the backend is told to fetch.

    Raises
    ------
    HTTPException
        No dataset has that id, or the dataset has no such row.
    """
    found = datasets.get(dataset)
    if found is None:
        raise HTTPException(status_code=404, detail="no such dataset")
    image = found.row_index.get(row)
    if image is None:
        raise HTTPException(status_code=404, detail="no such row")
    return str(image_addresses(found, image, base)["image_url"])


class PredictInput(BaseModel):
    """The address of the image to run a model on.

    Attributes
    ----------
    dataset : str
        The dataset id.
    row : int
        The absolute row of the pinned revision.
    """

    dataset: str
    row: int


async def watch_pool(registry: ModelRegistry, interval: float | None = None) -> None:
    """Ask every backend what it is, every :data:`PROBE_INTERVAL_SECONDS`, until cancelled.

    Parameters
    ----------
    registry : ModelRegistry
        The pool to ask.
    interval : float, optional
        Overrides the interval, for a test that cannot wait fifteen seconds. `None` reads
        :data:`PROBE_INTERVAL_SECONDS` when the loop starts, so patching it is enough to change the
        behaviour.

    Notes
    -----
    The probe runs in a worker thread, because the transport blocks: a three-second wait on a
    machine that is off must not hold the event loop that is answering everyone else. The first pass
    is one interval away rather than immediate, since the startup declaration has just been read.
    """
    pause = PROBE_INTERVAL_SECONDS if interval is None else interval
    while True:
        await asyncio.sleep(pause)
        await asyncio.to_thread(registry.probe)


def create_model_router(
    registry: ModelRegistry,
    datasets: Mapping[str, Dataset],
    data_base_url: str,
) -> APIRouter:
    """Build the model surface over a loaded registry and the dataset index.

    Parameters
    ----------
    registry : ModelRegistry
        The configured models and their backends, built at startup.
    datasets : mapping of str to Dataset
        The index, so a `(dataset, row)` in a predict body resolves the same way it does on the
        dataset surface.
    data_base_url : str
        The public distribution's base URL, with or without a trailing slash.

    Returns
    -------
    APIRouter
        The routes, ready to include. They inherit the request gate from the application, so nothing
        here authenticates and nothing here is anonymous.
    """
    base = data_base_url.rstrip("/")
    router = APIRouter(tags=["models"])

    @router.get(
        "/models",
        response_model=ModelDirectory,
        response_model_exclude_unset=True,
        summary="The model directory",
        description=(
            "One line per model: the side it runs at, how many backends are answering, and the "
            "control parameters it accepts."
        ),
    )
    def list_models() -> ModelDirectory:
        """List every model this gateway can be asked for.

        Returns
        -------
        ModelDirectory
            One entry per configured model, in id order.
        """
        return ModelDirectory(models=registry.directory())

    @router.get(
        "/models/{model}",
        response_model=ModelSummary,
        response_model_exclude_unset=True,
        summary="One model",
        description="What the model runs at and which parameters it accepts, freshly.",
    )
    def describe_model(model: str) -> ModelSummary:
        """Describe one model.

        Parameters
        ----------
        model : str
            The model id from the path.

        Returns
        -------
        ModelSummary
            The same entry the directory carries, which is why an id can be quoted.

        Raises
        ------
        HTTPException
            No model has that id.
        """
        return registry.describe(model)

    @router.post(
        "/models/{model}/predict",
        response_model=ModelPrediction,
        response_model_exclude_unset=True,
        summary="Run one inference",
        description=(
            "The image is addressed as `(dataset, row)` and resolved by the gateway, so a client "
            "never sends a URL one. Parameters are the model's own; `GET /models/{model}` lists "
            "them with their defaults, and omitting one uses the model's default."
        ),
    )
    def predict(
        model: str,
        request: Request,
        body: PredictInput,
        prompt: str | None = Query(None, description="The text the model is asked to find."),
        confidence_threshold: float | None = Query(
            None,
            ge=0.0,
            le=1.0,
            description="How sure the model must be before it reports an object.",
        ),
    ) -> ModelPrediction:
        """Segment one image named by an address.

        Parameters
        ----------
        model : str
            The model id from the path.
        request : Request
            The incoming request, whose query string is forwarded verbatim.
        body : PredictInput
            The address to resolve.
        prompt : str or None, optional
            The text prompt, where the model takes one.
        confidence_threshold : float or None, optional
            The confidence floor, where the model takes one.

        Returns
        -------
        ModelPrediction
            The model's own answer, already in this contract's geometry.

        Raises
        ------
        HTTPException
            No model has that id; the address does not resolve; or every backend failed.
        """
        registry.describe(model)  # refuses an unknown model before the address is even looked up
        url = _image_url(datasets, body.dataset, body.row, base)
        reply = registry.forward(
            model,
            path="predict",
            query=request.url.query,
            body=json.dumps({"input": {"url": url}}).encode(),
            content_type="application/json",
        )
        return _prediction(reply, model)

    @router.post(
        "/models/{model}/predict/upload",
        response_model=ModelPrediction,
        response_model_exclude_unset=True,
        summary="Run one inference on your own image",
        # The limit itself is enforced at the edge (deploy/Caddyfile): that is where the bytes
        # arrive, and where a chunked upload can be counted honestly. This entry is what tells a
        # reader of the reference that the limit exists at all.
        responses={413: {"description": "Over the 15 MB limit: compress or convert to JPEG."}},
        description=(
            "The participant's own photograph, sent as multipart and forwarded to the backend as "
            "bytes. It is never stored: this is the one path that carries bytes through the "
            "gateway, and it carries them no further than the model."
        ),
    )
    def predict_upload(
        model: str,
        request: Request,
        file: UploadFile,
        prompt: str | None = Query(None, description="The text the model is asked to find."),
        confidence_threshold: float | None = Query(
            None,
            ge=0.0,
            le=1.0,
            description="How sure the model must be before it reports an object.",
        ),
    ) -> ModelPrediction:
        """Segment one uploaded image.

        Parameters
        ----------
        model : str
            The model id from the path.
        request : Request
            The incoming request, whose query string is forwarded verbatim.
        file : UploadFile
            The image. Read into memory and forwarded; never written to disk.
        prompt : str or None, optional
            The text prompt, where the model takes one.
        confidence_threshold : float or None, optional
            The confidence floor, where the model takes one.

        Returns
        -------
        ModelPrediction
            The model's own answer.

        Raises
        ------
        HTTPException
            No model has that id, the upload is empty, or every backend failed.

        Notes
        -----
        Deliberately a plain `def`: the transport is a blocking socket call, so FastAPI runs the
        whole handler in its thread pool and an upload is one thread rather than the gateway. As an
        `async def` it made that call on the event loop and held every other route for the round
        trip — measured against the live gateway on 2026-10-17 with six concurrent uploads,
        `/health` answered in 11.08 s and 5.31 s, and in 1.2 ms once they drained. The sibling
        `predict` route is sync for the same reason; reading the body through `file.file` is what
        lets this one be.
        """
        registry.describe(model)
        payload = file.file.read()
        if not payload:
            raise HTTPException(status_code=400, detail="the upload is empty")
        reply = registry.forward(
            model,
            path="predict/upload",
            query=request.url.query,
            body=payload,
            content_type=file.content_type or "application/octet-stream",
        )
        return _prediction(reply, model)

    return router


def _refusal(reply: Reply) -> str:
    """Read a backend's own explanation out of a refusal, where it sent one.

    Parameters
    ----------
    reply : Reply
        The backend's refusal.

    Returns
    -------
    str
        The backend's message when it sent one in either shape we accept, and a plain line
        otherwise. The body is re-worded into this contract rather than forwarded: what a backend
        sends is its own business, and a caller reads one error shape here.
    """
    try:
        document = json.loads(reply.body)
    except json.JSONDecodeError:
        return "the backend refused the request"
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])
        if isinstance(document.get("detail"), str):
            return str(document["detail"])
    return "the backend refused the request"


def _prediction(reply: Reply, model: str) -> ModelPrediction:
    """Turn a backend's answer into this contract's body, or refuse.

    Parameters
    ----------
    reply : Reply
        The backend's successful answer.
    model : str
        The model id the request was for, used when the backend did not name one.

    Returns
    -------
    ModelPrediction
        The validated body.

    Raises
    ------
    HTTPException
        The backend answered something that is not this contract. That is a broken backend rather
        than a bad request, and it is reported as such: a participant cannot act on it and the
        operator can.
    """
    try:
        document = json.loads(reply.body)
        if isinstance(document, dict):
            document.setdefault("model", model)
        return ModelPrediction.model_validate(document)
    except (json.JSONDecodeError, ValueError) as error:
        logger.warning("backend answered a body that is not this contract: %s", error)
        raise HTTPException(
            status_code=502, detail="a backend answered outside the contract"
        ) from None
