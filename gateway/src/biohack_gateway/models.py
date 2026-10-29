"""The model surface: the pool's models answered over HTTP, and nothing else.

The gateway does exactly three things with a model request. It resolves the address a client named
into the public URL of an image, through the same index the dataset surface reads, so both surfaces
address a row identically. It forwards the request to a backend over one uniform protocol. And it
returns the backend's own answer, whose geometry the backend has already put into the shape this
contract publishes. A row prediction is also kept, keyed by the dataset's published revision, and a
repeat of the same question is answered without reaching a machine at all; an upload is never kept,
because its image is the caller's rather than the dataset's.

Nothing here encodes geometry, holds a weight, or knows what a mask is. :class:`ObjectOut` and
:class:`RleMask` are imported from the dataset surface rather than written again: a second
definition of the same body is the one thing this surface must not grow, and `gateway.md` asks for
exactly one object shape across the two surfaces so a participant writes one decoder.

Routing is the whole algorithm — try a model's backends in order of expected wait, pass over the one
that is gone for at least as long as the failure cost, move on. What counts as gone is narrow on
purpose: a machine that did not answer, one that answered as a proxy which cannot reach its model,
and a run of `500`s that says the application is failing rather than this request. A request a
backend could not handle is answered and does not take the pool with it, and a timeout while a body
was still going out is charged to the link rather than to the machine. There is no circuit breaker
and no weighted choice, which are for a pool larger than this one. Availability is probed on a clock
of its own (:data:`PROBE_INTERVAL_SECONDS`), because what a request observes is a sample, and
:data:`RESPONSE_TIMEOUT_SECONDS` is what a dead backend costs a caller.

**Control parameters come from the model, not from here.** A model declares what it accepts — the
prompt it can be given, the boxes and points it can be aimed with, the threshold it applies, the
sizes it was run at — and the gateway repeats that declaration in ``GET /models``, refuses a name or
a value the declaration does not answer for, and forwards the rest verbatim. The declaration is the
one place the parameters appear: no parameter name is written in this package, so a new model needs
no gateway change, the same bargain the dataset surface strikes with a published index.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import math
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

from biohack_gateway.admission import UploadQueue, UploadRefused
from biohack_gateway.cache import CachedAnswer, ResultCache, canonical_request
from biohack_gateway.dataset_index import Dataset
from biohack_gateway.datasets import ObjectOut, image_addresses
from biohack_gateway.limits import ConcurrencyGate

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

DELAY_PER_QUEUED_REQUEST = {"predict": 0.25, "predict/upload": 0.5}
"""Seconds a request already in flight on a machine adds to the expected wait of the next one.

Measured 2026-10-19 by holding 50 requests against the pool and reading each attempt's duration
against its depth at dispatch: one request ahead cost a fraction of a service time rather than a
whole one. The reason is not parallel inference — each container holds a lock around its GPU work
and runs one inference at a time — it is that the part of a request which is *not* GPU work, the
image fetch and the body's transfer, overlaps with the inference the card is already doing.

That is why the row figure is the smaller of the two: a published row's image is fetched by the
backend while the lock is free, whereas an upload's body arrives on the request itself.

The depth these figures multiply is the machine's, not the container's, because a card carrying
several models is still one GPU. What it is for, and the load test behind it, is
`.agents/references/202610192300_lane-width-and-the-score.md`.
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

It is a ceiling on the *connect phase*, not on the request: a fallback handed only the rest of
:data:`RESPONSE_TIMEOUT_SECONDS` gets the smaller of the two, so the attempt cannot outlive the
one budget the caller was given.
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

It is a **wall-clock budget for the whole exchange**, not a socket-inactivity timeout. The
transport records a monotonic deadline when it is called and ends the connect, send and read
phases against that one deadline, so a response that drips a byte at a time cannot outlast the
budget by resetting an inactivity clock on each byte (measured 2026-10-21: 15 bytes at one per
0.04 s took 0.56 s against a 0.1 s budget, because each read was a fresh timeout). A request's
retries share the same clock: :meth:`ModelRegistry.forward` gives the first backend the whole
budget and each fallback only what the attempts before it left.
"""

assert UNHEALTHY_SECONDS >= RESPONSE_TIMEOUT_SECONDS, (
    "a failed backend must be passed over for at least as long as the failure it remembers cost"
)

MIN_SEND_BYTES_PER_SECOND = 250_000.0
"""The slowest path an upload's send may finish on before it counts as a failure.

The send and the answer are bounded separately because different things limit them: the answer is
the machine working, which has a measured ceiling, while the send is this host's route to the
machine, whose rate belongs to the network of the day. Fifteen megabytes at this floor is a minute,
which is generous on purpose — a body already accepted from a participant is worth delivering, and a
send that is merely slow must not be recorded as a machine that failed.

:meth:`ModelRegistry.forward` turns this floor into a **send allowance** and adds it to the one
deadline it shares across attempts. The transport then enforces that single number, so a slow send
consumes its own allowance and leaves :data:`RESPONSE_TIMEOUT_SECONDS` for the answer instead of
resetting a clock per read.
"""

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
"""The largest answer one backend may send before the attempt is discarded.

**A host-resource policy, not the mathematical maximum of a legal answer.** A segmentation answer
is JSON carrying RLE masks, and a complex mask or a larger number of instances can be much bigger
than any figure extrapolated from one sample: the RLE ratio of the 173,716-pixel, 2,184-character
mask measured on 2026-10-16 does not bound a many-instance or high-entropy mask. There is no
derivation from the contract that produces a byte maximum, and this constant does not pretend to be
one.

What it is chosen from is the observed scale plus a wide margin. The largest answer in the
2026-10-19 load run was 26,216 bytes; sixteen mebibytes is about 640 times that, which keeps an
ordinary mask, an unusually detailed one and a many-instance answer inside the ceiling without
asking an operator to tune anything at an event.

It is deliberately **not** a promise that no legitimate answer is ever refused. An answer above it
is treated as a broken machine: the reply is ``status=0``, the connection is closed and not pooled,
and another backend may be tried. If real traffic ever produces a legitimate answer above sixteen
mebibytes, this constant is the knob to revisit, with the host's memory in mind; it is not an
environment variable, because a deployment that raises it without raising the memory budget would
turn a protection into a way to exhaust the process.

The resource it protects is bounded, not eliminated. A body is held whole for the caller, and the
eight upload slots (:data:`biohack_gateway.admission.DEFAULT_UPLOAD_SLOTS`) can each carry one, so
the upload path's worst case is eight answers of this size on top of the edge's 15 MB bodies — large
enough to matter on this host, and still a computable ceiling instead of an unbounded one. The read
loop stops at one byte over the ceiling, and an announced ``Content-Length`` is refused before the
body is read at all.
"""

RESPONSE_READ_CHUNK = 64 * 1024
"""Bytes one progress read asks for.

``HTTPResponse.read1`` returns what the socket produced rather than filling the request, so this is
only an upper bound on a single iteration; the read loop rechecks the deadline between chunks.
64 KiB is large enough that a normal answer takes a handful of reads and small enough that an
abnormal one never grows the buffer by more than one extra chunk before the ceiling stops it.
"""

MACHINE_STATUSES = frozenset({502, 503, 504})
"""Answers that say the machine is not serving, rather than that one request failed.

A proxy in front of the model — or the model's own gateway — answers these when it cannot reach what
it fronts, so they are what a machine that is gone looks like. `500` is deliberately not here: it is
one request the backend could not handle, and the next request may be fine.
"""

MAX_CONSECUTIVE_SERVER_ERRORS = 3
"""`5xx` answers in a row after which the machine is passed over anyway.

Two things are true at once about a `500`: one crashing request must not take a whole model off the
pool, and a container that answers `500` to everything must not keep taking traffic forever. A short
run of them is the line between the two — the probe cannot see this case, because a backend whose
application fails still answers `/health` with `200`.
"""

DEFINITIVE_FAILURES = frozenset({"refused", "unreachable", "oversized"})
"""Failure kinds that establish something about this attempt, so one of them passes a backend over.

A refusal is the machine answering with a reset: it is up and nothing listens on the port. An
oversized answer is the machine answering with something it must not send. ``unreachable`` is a
route or a name that is not there, or a connection the peer closed — after the transport's one retry
on a fresh socket, which is what keeps a stale keep-alive out of this.

A **timeout** is deliberately not here: it is the one kind a dropped packet produces, so it is the
one that has to happen twice.
"""

AMBIGUOUS_FAILURES_BEFORE_PASSING_OVER = 2
"""How many timeouts in a row it takes before a backend is passed over.

A timeout looks identical whether the card is switched off or a packet was dropped, and on
2026-10-29 from the laboratory LAN about 2% of TCP segments were being retransmitted to every
off-campus destination while a 2 kB JSON call took 9.07 s. A single timeout is therefore evidence
about the path. Charging it to the machine took a backend out of the pool for
:data:`UNHEALTHY_SECONDS` exactly when the link was already slow, and the pool is what a participant
is told to wait for.

The second in a row is the line: a machine that is really gone fails every attempt, and the probe
asks again every :data:`PROBE_INTERVAL_SECONDS` regardless, so nothing stays hidden for long.
"""

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
    """One control parameter a model accepts, as the model itself declares it.

    The shape is deliberately a fragment of a control protocol rather than a description: the
    gateway validates a request against these fields, so what a model publishes here is what a
    caller is allowed to send and nothing else.

    Attributes
    ----------
    name : str
        The parameter's name, which is the query parameter's name on the predict routes. Copied
        from the model's own vocabulary — a wrapper, not a dialect — so two models may name the
        same idea differently and both are right.
    type : str
        How a value is read: ``text``, ``number``, ``boolean``, ``enum``, ``boxes`` or ``points``.
        The last two are JSON arrays, one per parameter, in the image's own pixels.
    default : str or float or bool or None
        What the model uses when the client sends nothing, where it has a default.
    minimum, maximum : float or None
        The accepted range, where the parameter is numeric and bounded.
    values : list of float or None
        The sizes or steps the model was actually run at, where a range would promise more than was
        measured. A value above the largest is governed by :attr:`above_maximum`.
    enum : list of str or None
        The accepted values, where the parameter is one of a set.
    max_length : int or None
        How long a text value may be, in characters.
    required : bool
        Whether a request must carry it.
    requires, excludes : list of str
        Names that must be present with it, and names that must not be.
    above_maximum : str
        ``reject`` (the default) refuses a value above the ceiling; ``clamp`` lets it through, for
        the container to answer at the ceiling and report that it did.
    description : str
        One line saying what the parameter does, safe to publish.
    """

    name: str
    type: Literal["text", "number", "boolean", "enum", "boxes", "points"] = "text"
    default: str | float | bool | None = None
    minimum: float | None = None
    maximum: float | None = None
    values: list[float] | None = None
    enum: list[str] | None = None
    max_length: int | None = None
    required: bool = False
    requires: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    above_maximum: Literal["reject", "clamp"] = "reject"
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
    declared : bool
        Whether any backend has ever answered what it serves. `False` means the parameter list below
        is empty because nothing has said anything — not because the model takes nothing. The
        distinction decides whether a query is validated at all, so it is published rather than
        inferred from an empty list.
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
    declared: bool = False
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


class MachineSummary(BaseModel):
    """One machine in the pool, as the router counts it — the unit a queue belongs to.

    This is the operator's view of the pool — `GET /pool` — and not part of the participant
    contract: it carries a machine's own name, which is an inventory fact, where the published model
    surface deliberately carries only the reproducibility record of the machine that answered.

    Attributes
    ----------
    machine : str
        The name the containers on it declare, or one container's own URL where it declared none —
        the same key :attr:`Backend.machine_key` groups by, so the depth here is the depth the
        router scored with.
    models : list of str
        The models whose backends run on it, in id order. A card carrying three containers has
        three.
    alive : bool
        Whether any of its backends answered the last probe. A machine with one answering container
        and one loading is up: something on it can take work.
    rtt_ms : float or None
        The best measured round trip to it, in milliseconds. `None` until a probe answered, which
        is why it is optional rather than zero — "not measured" and "instantaneous" are different
        facts.
    in_flight : int
        Requests this gateway has running against it, summed across every model it serves.
    probed_seconds_ago : int or None
        How long ago its newest probe answered, to the nearest second. `None` where it has never
        been asked, because a verdict without its age is a promise about the past.
    last_failure : str or None
        The kind of the most recent failed attempt against it, with the phase for a timeout —
        ``"timeout at connect"``, ``"refused"``, ``"status"``. `None` until something failed.

        This is what `alive` cannot say. A machine that is alive *and* timed out a second ago is a
        path problem; one that is not alive because it refused is a container that is not running.
        Measured on 2026-10-29: the campus egress was dropping about 2% of TCP segments, and
        without this an operator reading the pool saw a card disappear and had no way to tell it
        from a power cut.
    failed_seconds_ago : int or None
        How long ago that was, to the nearest second. `None` where nothing has failed.
    failures : int
        Failed attempts since the process started, requests and probes together, summed across its
        containers. The age says whether it is passing; this says how much of it there has been.
    passed_over : bool
        Whether every container on it is inside its thirty seconds of quiet — the request path's
        own quarantine, which is not what `alive` says. The router sends a machine work only where
        both hold: `alive` true and this false.
    """

    machine: str
    models: list[str] = field(default_factory=list)
    alive: bool = False
    rtt_ms: float | None = None
    in_flight: int = 0
    probed_seconds_ago: int | None = None
    last_failure: str | None = None
    failed_seconds_ago: int | None = None
    failures: int = 0
    passed_over: bool = False


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


class ModelWarning(BaseModel):
    """Something a model did that the request did not ask for, and the request was not refused.

    A clamp is the case this exists for: the container answers at its ceiling and says so. It keeps
    the error contract's two-part shape — a stable code a program branches on and a sentence a human
    reads — without being an error, because the answer is a real answer.

    Attributes
    ----------
    code : str
        Stable discriminant, such as ``resolution_clamped``.
    message : str
        One line saying what was done instead, safe to surface to the caller.
    """

    code: str
    message: str


class ModelPrediction(BaseModel):
    """The answer to a predict request, whatever the model was.

    Attributes
    ----------
    model : str
        The model that answered.
    objects : list of ObjectOut
        The same object shape the dataset surface returns, so one decoder reads both.
    count : float or None
        The number of objects a counting model found, where it reports one. `None` when the model
        reports none, and absent from a mask answer's wire shape. A counting model answers this
        instead of objects, and still sends `objects: []`: one shape serves both.
    points : list of list of float or None
        Where a counting model placed what it counted, as ``[x, y, score]`` in the original image's
        pixels. `score` is relative salience within this one answer, used for ordering — not a
        calibrated confidence, and not comparable across images or models. `None` or absent when the
        model places nothing.
    resolution_used : int or None
        The side the model actually ran at, where it reports one. This is the field that explains a
        mask's blockiness, and the one that makes a clamped `imgsz` visible without a warning.
    computed_by : ComputedBy or None
        The machine that produced the answer, where the backend reports one. It is repeated
        unchanged when the answer comes from the cache: it records the machine that actually ran,
        and the cache ran nothing.
    warnings : list of ModelWarning
        Anything the model did instead of what was asked. Absent from an answer that needed none,
        because the field is additive and the gateway relays rather than invents.
    cached : bool
        Whether this answer was served from the cache rather than computed for this call. Absent
        from a freshly computed answer, so a live answer's wire shape is unchanged.
    cached_at : datetime or None
        When the stored answer was computed, present exactly when :attr:`cached` says it was
        stored. A caller comparing two answers needs to know one of them is from the past.
    """

    model: str
    objects: list[ObjectOut]
    count: float | None = None
    points: list[list[float]] | None = None
    resolution_used: int | None = None
    computed_by: ComputedBy | None = None
    warnings: list[ModelWarning] = field(default_factory=list)
    cached: bool = False
    cached_at: datetime | None = None


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
    failure : str or None
        What failed, where the transport can tell: ``"timeout"``, ``"refused"``,
        ``"unreachable"`` or ``"oversized"``. `None` for an answer — a status, whatever it is —
        because a `500` is the machine working, badly, and the classification of that one belongs
        to whoever knows the status codes.

        It is recorded rather than inferred from `status` because the difference decides two things
        an operator needs: whether one attempt is enough to take a machine out of the pool, and
        which of a dead machine and a lossy link a reader of `/pool` is looking at.
    stage : str or None
        Which half of the exchange the clock ended, when it did: ``"connect"`` while the socket was
        being opened, ``"send"`` while the body was still going out, ``"read"`` while waiting for an
        answer. `None` otherwise.

        The three are different facts about different things. A connect that did not finish, like an
        outright refusal, says the machine is not reachable and belongs to the pool's health. A slow
        send is this host's path to the machine — a 15 MB body over a degraded link — and a send the
        clock ended says nothing about whether the machine is serving, so treating it as a failed
        backend shut a healthy one out of the pool and repeated the same slow send against the next.
        A read the clock ended is the machine owing an answer for longer than the request's budget;
        a fallback that was handed only the budget left by earlier attempts is the request's clock
        rather than a verdict on that machine, which :meth:`ModelRegistry.forward` distinguishes.
    """

    status: int
    body: bytes
    timed_out: bool = False
    stage: Literal["connect", "send", "read"] | None = None
    failure: str | None = None


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

    connect_timeout: float = CONNECT_TIMEOUT_SECONDS
    """The budget for one handshake.

    The transport narrows it to whatever is left of the request's own deadline, so a fallback cannot
    spend three seconds on a connect when the caller has one second left.
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
        self.sock = socket.create_connection((self.host, self.port), self.connect_timeout)
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

More than a handful would sit unused: a machine carried up to five simultaneous requests in the
2026-10-19 run, and that overlap is image fetch and transfer rather than inference, so four idle
connections are enough to keep a burst from paying a handshake. The gateway's own thread pool is
what bounds simultaneous calls.
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


class _DeadlineWatchdog:
    """Shut a connected socket down when a monotonic deadline passes.

    ``http.client`` reads the status line, every header line and every chunk-size line with its own
    ``recv``, and a socket timeout applies to one such call: a peer that drips header bytes never
    waits long enough for any single timeout to fire, so the request can outlive its budget while
    every individual read looks healthy. The ``read1`` loop bounds an ordinary body, but not the
    library's header parsing or chunk framing, and replacing that parser here would be a second HTTP
    decoder.

    A timer that shuts the socket down is the one bound that covers whatever the library does inside
    ``getresponse()`` and between chunks: ``shutdown`` wakes a blocked ``recv`` or ``sendall`` on
    another thread without closing the descriptor under the reader. The timer is stopped and joined
    before its socket may return to the idle pool, and it records whether it fired so a socket it
    shut down is never kept.
    """

    def __init__(self, sock: socket.socket, deadline: float) -> None:
        """Prepare a watchdog for one connected socket.

        Parameters
        ----------
        sock : socket.socket
            The connected socket to shut down at the deadline.
        deadline : float
            The monotonic instant the exchange must have ended by.
        """
        self._socket = sock
        self._lock = threading.Lock()
        self._fired = False
        self._finished = False
        self._timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._shut_down)
        self._timer.daemon = True

    @property
    def fired(self) -> bool:
        """Whether the deadline passed and the socket was shut down.

        Returns
        -------
        bool
            True once the timer has run. Read under the same lock that ``stop`` uses, so a
            successful exchange cannot report a socket as safe while a shutdown is in progress.
        """
        with self._lock:
            return self._fired

    def arm(self) -> None:
        """Start the timer.

        Called after the connection exists and before the request is written, so the send and the
        whole answer are covered by one shutdown instant.
        """
        self._timer.start()

    def _shut_down(self) -> None:
        """End every blocking operation on the socket, or do nothing if the exchange finished."""
        with self._lock:
            if self._finished:
                return
            self._fired = True
            # Already closed by the request path is fine; the deadline still stands.
            with contextlib.suppress(OSError):
                self._socket.shutdown(socket.SHUT_RDWR)

    def stop(self) -> bool:
        """Cancel the timer and report whether the socket may be kept.

        Returns
        -------
        bool
            True when the timer never fired, so the socket was not shut down and may go back to the
            pool. False once the watchdog has shut it down.

        Notes
        -----
        The lock is held while the flag is set and the shutdown itself runs, so the two outcomes
        cannot interleave: either ``stop`` wins and the timer becomes a no-op, or the timer wins and
        ``stop`` reports the socket as unusable. Joining outside the lock waits for a timer that had
        already started, so nothing can shut the socket down after it has been checked in.
        """
        self._timer.cancel()
        with self._lock:
            self._finished = True
            fired = self._fired
        self._timer.join()
        return not fired


class _ResponseTooLarge(Exception):
    """A backend answered with more bytes than :data:`MAX_RESPONSE_BYTES`.

    Kept separate from :class:`TimeoutError` because the two are different verdicts: an oversized
    answer is the backend's fault and the machine should be passed over, while a clock that ended
    may be the request's own shared budget rather than a fact about the machine.
    """


def _read_response(
    response: http.client.HTTPResponse,
    connection: http.client.HTTPConnection,
    deadline: float,
) -> bytes:
    """Read one response body under a single deadline and a single size ceiling.

    Parameters
    ----------
    response : http.client.HTTPResponse
        The response whose body is still unread.
    connection : http.client.HTTPConnection
        The connection whose socket timeout is reset before every read, so an inactivity timeout
        can never outlive the deadline.
    deadline : float
        The monotonic instant the whole exchange must have ended by.

    Returns
    -------
    bytes
        The body. It is at most :data:`MAX_RESPONSE_BYTES` long.

    Raises
    ------
    TimeoutError
        The deadline passed before the body ended.
    _ResponseTooLarge
        The body announced, or delivered, more than :data:`MAX_RESPONSE_BYTES`.

    Notes
    -----
    ``read1`` rather than ``read``: ``read`` asks for the whole body and only returns when it has
    it, so a peer that drips a byte every few milliseconds keeps resetting the socket's inactivity
    timeout and the call outlives its budget (measured 2026-10-21: 15 bytes at one per 0.04 s took
    0.56 s against a 0.1 s budget). ``read1`` returns what one socket read produced, which brings
    this loop back between body chunks to check the wall clock.

    This loop therefore bounds an ordinary body; it does **not** bound the status line, the headers
    or chunk-size lines, which ``http.client`` reads inside ``getresponse()`` and between chunks.
    :class:`_DeadlineWatchdog` is what ends those, by shutting the socket down at the same deadline.
    """
    if response.length is not None and response.length > MAX_RESPONSE_BYTES:
        raise _ResponseTooLarge
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        # One byte over the ceiling is enough to know the answer is too large; asking for exactly
        # that much keeps a single iteration from buffering an unbounded remainder.
        chunk = response.read1(min(RESPONSE_READ_CHUNK, MAX_RESPONSE_BYTES - total + 1))
        if not chunk:
            if response.length not in (None, 0):
                # Unlike read(), read1() does not raise for a short Content-Length body. Do not
                # accept a valid JSON prefix from an incomplete HTTP exchange or return its socket.
                raise http.client.IncompleteRead(b"", response.length)
            return b"".join(chunks)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge
        chunks.append(chunk)


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
        The whole budget for this call, from the first connect attempt to the last byte of the
        answer. :meth:`ModelRegistry.forward` passes a request's remaining budget, so retries share
        one clock; the connect phase is additionally capped by :data:`CONNECT_TIMEOUT_SECONDS`.

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

    **The deadline is a wall clock for the whole exchange, not a socket-inactivity timeout.** It is
    set before the connect and every phase narrows the socket to what is left of it; the connect
    phase is additionally capped by :data:`CONNECT_TIMEOUT_SECONDS`. A socket timeout only bounds
    one blocking call, and ``http.client`` makes many for the status line, each header line and each
    chunk header, so a peer that drips one byte at a time can outlive it while every individual read
    looks healthy. A watchdog (:class:`_DeadlineWatchdog`) shuts the connected socket down at the
    deadline instead, waking whichever read the library is in without this module re-implementing
    its parser. A shutdown during a header read can look like a complete, empty answer, so an
    exchange the watchdog ended is reported as a timeout even if a body arrived at the edge.

    The socket's timeout is set for every phase of every call, whether the connection was made here
    or taken from the pool: ``HTTPConnection`` reads its timeout only when it creates a socket, so a
    kept connection would otherwise keep the probe's three seconds — which is how a predict that
    reused one timed out at three seconds.

    A body over :data:`MAX_RESPONSE_BYTES` ends the attempt as an unreachable-machine failure rather
    than as an answer: the connection is closed instead of pooled, and the caller may try another
    backend. The bytes of the oversized answer are never held beyond one read chunk.
    """
    parsed = urllib.parse.urlsplit(url)
    factory = _CONNECTIONS.get(parsed.scheme)
    if factory is None or not parsed.hostname:
        logger.warning("backend url is not an http(s) address: %s", url)
        return Reply(status=0, body=b"", failure="unreachable")

    key = (parsed.scheme, parsed.hostname, parsed.port or 0)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    deadline = time.monotonic() + timeout

    for attempt in (0, 1):
        connection = _checkout(key) if attempt == 0 else None
        reused = connection is not None
        if connection is None:
            connection = factory(parsed.hostname, parsed.port, timeout=timeout)
        stage: Literal["connect", "send", "read"] = "connect"
        watchdog: _DeadlineWatchdog | None = None
        keep = False
        try:
            if connection.sock is None:
                connect_budget = min(CONNECT_TIMEOUT_SECONDS, deadline - time.monotonic())
                if connect_budget <= 0.0:
                    raise TimeoutError
                if isinstance(connection, _BoundedHTTPConnection):
                    connection.connect_timeout = connect_budget
                else:
                    # The standard library's ``HTTPSConnection`` reads ``self.timeout`` when its
                    # handshake creates the socket, and has no separate connect seam.
                    connection.timeout = connect_budget
                connection.connect()
            sock = connection.sock
            if sock is None:
                raise ConnectionError("the connection has no socket")
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError
            connection.timeout = remaining
            sock.settimeout(remaining)
            # The socket timeout above bounds one blocking call. The library makes many of them for
            # a status line, each header line and each chunk header, so it is not a bound on the
            # exchange; the watchdog is, and it covers those reads without a second HTTP parser.
            watchdog = _DeadlineWatchdog(sock, deadline)
            watchdog.arm()
            stage = "send"
            connection.request(method, target, body=body, headers=dict(headers))
            # The send may have spent most of the budget, so the answer gets what is actually left
            # rather than the timeout computed before the body went out.
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError
            connection.timeout = remaining
            sock.settimeout(remaining)
            stage = "read"
            response = connection.getresponse()
            try:
                payload = _read_response(response, connection, deadline)
                if watchdog.fired:
                    # A shutdown while headers were being read can look like a complete, empty
                    # answer: EOF is indistinguishable from a zero-length body. An answer that was
                    # only completed at the clock's edge is therefore not trusted as an answer.
                    raise TimeoutError
            finally:
                response.close()
        except _ResponseTooLarge:
            connection.close()
            logger.warning(
                "backend %s answered more than %d bytes; closing the connection",
                parsed.hostname,
                MAX_RESPONSE_BYTES,
            )
            return Reply(status=0, body=b"", failure="oversized")
        except TimeoutError:
            connection.close()
            return Reply(status=0, body=b"", timed_out=True, stage=stage, failure="timeout")
        except ConnectionError as error:
            # `RemoteDisconnected` is a `ConnectionResetError`, and a reset or a broken pipe here is
            # the peer having closed a connection we were keeping. Retried once, never twice, and
            # only when the connection came from the pool: one made for this attempt failing is a
            # backend that is down, not a window that ran out.
            connection.close()
            if watchdog is not None and watchdog.fired:
                # A shutdown produced the reset; it is the request's clock, not a stale keep-alive.
                return Reply(status=0, body=b"", timed_out=True, stage=stage, failure="timeout")
            if reused:
                logger.info("a kept connection to %s had closed; asking again", parsed.hostname)
                continue
            logger.warning("backend unreachable: %s (%s)", url, error)
            return Reply(status=0, body=b"", failure=_connection_failure(error))
        except (OSError, http.client.HTTPException) as error:
            # Connection refused, no route, DNS, a bad status line, a truncated answer — one outcome
            # for all of them. A ``TimeoutError`` is caught above; any other operating-system
            # failure here is not a timeout, unless the watchdog caused it.
            connection.close()
            if watchdog is not None and watchdog.fired:
                return Reply(status=0, body=b"", timed_out=True, stage=stage, failure="timeout")
            logger.warning("backend unreachable: %s (%s)", url, error)
            return Reply(status=0, body=b"", failure=_connection_failure(error))
        finally:
            # Bound to the success path too: a fired watchdog means the socket was shut down and
            # must not be pooled, and joining here is what makes that decision race-free.
            if watchdog is not None:
                keep = watchdog.stop()
        if response.will_close or not keep:
            connection.close()
        else:
            _checkin(key, connection)
        return Reply(status=response.status, body=payload)
    return Reply(status=0, body=b"", failure="unreachable")


def _connection_failure(error: BaseException) -> str:
    """Name what a failed connect or exchange established about the machine.

    Parameters
    ----------
    error : BaseException
        The operating-system or protocol error that ended the attempt.

    Returns
    -------
    str
        ``"refused"`` when something answered and nothing is listening on the port — proof the
        machine is up and its container is not — and ``"unreachable"`` for everything else: no
        route, an unreadable name, a peer that accepted and then closed, a truncated answer.

    Notes
    -----
    The two are not decoration. A refusal is a fact about the machine — something answered, and
    nothing listened — so one of them retires the backend. Everything else in this bucket is a fact
    about *this* host's route to it, which retires it too but for a different reason: retrying a
    route that is not there, a name that does not resolve, or a peer that closes a fresh connection
    gains nothing, and the probe is the thing that asks again on its own clock.
    ``ConnectionRefusedError`` is the operating system's own distinction and needs no errno
    inspection.
    """
    return "refused" if isinstance(error, ConnectionRefusedError) else "unreachable"


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
    rtt_seconds : float
        Exponential moving average of the round trip to this backend, from the probe that answers
        `/health`. Zero means not yet measured: a backend only gets a figure from a `200`, so a
        container that is still loading is not credited with the speed of its refusal. Five samples
        of weight 0.2 each are what make it follow a route that changed without one slow probe
        reordering the pool.
    in_flight : int
        Attempts this gateway currently has running against this backend. Read when choosing which
        backend to try, so that a request arriving while another is in flight to one that is in fact
        dead does not queue behind that backend's own timeout.
    server_errors : int
        Consecutive answers in the `5xx` range, reset by any success, by a network failure, and by
        anything else that is not the application failing. A `500` is one request the backend could
        not handle rather than a machine that is gone, so it takes
        :data:`MAX_CONSECUTIVE_SERVER_ERRORS` of them before the machine is passed over: the first
        one must not shut a healthy backend out of the pool, and a container that answers `500` to
        everything must not keep taking traffic.
    ambiguous_streak : int
        Timeouts in a row, reset by any success, by a probe that answered, and by any other kind of
        failure. :data:`AMBIGUOUS_FAILURES_BEFORE_PASSING_OVER` of them are what take the backend
        out of the pool, because one lost packet and one dead card look the same to one attempt.
    last_failure : str or None
        The kind of the most recent failed attempt, requests and probes alike: ``"timeout"``,
        ``"refused"``, ``"unreachable"``, ``"oversized"`` or ``"status"``. Never cleared by a
        success — a machine that is answering *and* timed out a second ago is the whole point, and
        `/pool` is where an operator reads that.
    last_failure_stage : str or None
        Where the clock ended, for a timeout: ``"connect"``, ``"send"`` or ``"read"``. `None`
        otherwise. A connect timeout is the ambiguous one; a send timeout belongs to this host's
        link and a read timeout to the machine owing an answer.
    last_failure_at : float
        When that failure happened, as a monotonic stamp, so `/pool` can age it. Zero means never.
    failures : int
        Failed attempts since the process started, requests and probes together — the count that
        says whether a kind is a passing event or the state of things.
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
    rtt_seconds: float = 0.0
    in_flight: int = 0
    server_errors: int = 0
    ambiguous_streak: int = 0
    last_failure: str | None = None
    last_failure_stage: str | None = None
    last_failure_at: float = 0.0
    failures: int = 0
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

    @property
    def machine_key(self) -> str:
        """The machine whose queue this container's requests belong to.

        Returns
        -------
        str
            The name the container declared in ``computed_by.machine``, or its own URL when it
            declared none — a machine that cannot be named is counted alone rather than merged with
            every other unnamed one.

        Notes
        -----
        Two containers on one machine declare the same name, and that is the point: a card serving
        three models has one GPU, so a request for the third must see the work the other two are
        already doing. The name comes from the run line (`SAM31_MACHINE` and its siblings), which is
        also why the same machine named two different ways splits its own queue in two — the one
        mistake this key cannot detect.
        """
        if self.machine is not None and self.machine.machine:
            return self.machine.machine
        return self.url


def _failure_kind(reply: Reply) -> str:
    """Name what a failed attempt established about the machine that failed it.

    Parameters
    ----------
    reply : Reply
        An answer that was not a `2xx`, or no answer at all.

    Returns
    -------
    str
        ``"timeout"``, ``"refused"``, ``"unreachable"`` or ``"oversized"`` as the transport named
        it; ``"status"`` for an answer the machine sent, whatever its code.

    Notes
    -----
    The transport's own classification wins where it has one. A `Reply` built without it — a test
    double, or a transport that is not :func:`http_transport` — is read the only way its fields
    allow: a clock that ended means a timeout, no status means the machine could not be reached, and
    a status means the machine answered.
    """
    if reply.failure is not None:
        return reply.failure
    if reply.timed_out:
        return "timeout"
    return "unreachable" if reply.status == 0 else "status"


def _record_failure(backend: Backend, reply: Reply, kind: str, now: float) -> bool:
    """Record one failed attempt and say whether it is a verdict on the machine.

    Parameters
    ----------
    backend : Backend
        The backend the attempt was made against, mutated with what the failure was.
    reply : Reply
        The failed answer.
    kind : str
        What :func:`_failure_kind` named it.
    now : float
        The current monotonic clock.

    Returns
    -------
    bool
        True when this failure is enough to pass the backend over, False when it is one more
        observation to wait on.

    Notes
    -----
    **One place decides what a failure means**, so a request and a probe cannot disagree about the
    same evidence. The rule is what a lost packet can and cannot produce:

    - ``refused``, ``unreachable`` and ``oversized`` are about this attempt and are a verdict on
      their own.
    - ``timeout`` is also what a broken link looks like, so it is a verdict only at
      :data:`AMBIGUOUS_FAILURES_BEFORE_PASSING_OVER` in a row.
    - ``status`` keeps its own rules: a `502`/`503`/`504` is a machine that is not serving, while a
      `500` is one request the application could not handle and takes
      :data:`MAX_CONSECUTIVE_SERVER_ERRORS` of them.

    The counters are kept apart for a reason: a network failure resets the `5xx` run, because it
    says nothing about the application, and a definitive failure clears the ambiguous streak,
    because the two were never the same question.
    """
    backend.last_failure = kind
    backend.last_failure_stage = reply.stage
    backend.last_failure_at = now
    backend.failures += 1
    if kind == "status":
        backend.ambiguous_streak = 0
        backend.server_errors += 1
        return (
            reply.status in MACHINE_STATUSES
            or backend.server_errors >= MAX_CONSECUTIVE_SERVER_ERRORS
        )
    backend.server_errors = 0
    if kind in DEFINITIVE_FAILURES:
        backend.ambiguous_streak = 0
        return True
    backend.ambiguous_streak += 1
    return backend.ambiguous_streak >= AMBIGUOUS_FAILURES_BEFORE_PASSING_OVER


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
        # Guards `Backend.in_flight` and the per-machine counts, and is held for an increment and a
        # decrement. The sync predict routes run in FastAPI's thread pool, so two requests really do
        # touch one backend's counter at the same time.
        self._lock = threading.Lock()
        self._machine_in_flight: dict[str, int] = {}
        """Requests in flight per machine, summed across every model it serves.

        The unit a caller waits on is a card, not a container: one GPU answering three models has
        three containers and one queue, and a request for the third model that saw only its own
        container's depth would be told the machine is free while two inferences run on it.
        """
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
            The backend to ask. `alive`, `answered`, `probed_at` and `rtt_seconds` are updated from
            the answer.

        Returns
        -------
        Reply
            The answer, for a caller that also wants the declaration out of it.

        Notes
        -----
        A `200` is the only answer that counts as alive: a container still loading its weights
        answers `503`, and publishing it as up would promise a call it would refuse.

        A failure is read through :func:`_record_failure`, so a probe and a request cannot disagree
        about the same evidence: one timeout leaves the machine's recorded state alone, because a
        dropped probe packet and a switched-off card look identical, and a refusal or a second
        timeout is what turns `alive` off. A `200` from the probe also clears the request path's
        streak — it is the one witness that is not competing with traffic for the machine.

        The same call is the round-trip measurement, because it is the only request the gateway
        makes to a backend that carries no work: its duration is the link plus the answer, nothing
        else. Only a `200` is folded in — a refusal from a container that is still starting measures
        the container, not the route — and the first one is *assigned* rather than blended with the
        `0.0` seed, which stands for "never measured" and not for an observation.
        """
        started = time.monotonic()
        reply = self._transport(
            "GET", f"{backend.url}/health", {}, None, DECLARATION_TIMEOUT_SECONDS
        )
        elapsed = time.monotonic() - started
        now = time.monotonic()
        if reply.status == 200:
            backend.alive = True
            backend.ambiguous_streak = 0
        elif _record_failure(backend, reply, _failure_kind(reply), now):
            # The probe is what tells a machine nobody is calling apart from one that is answering,
            # so its verdict is what `backends_up` publishes. It obeys the same rule as a request —
            # one timeout is a lost packet, two are a machine — and its own success is what clears
            # the streak, because a machine that answers `/health` is not a machine that is gone.
            backend.alive = False
        backend.answered = reply.status != 0
        backend.probed_at = now
        if backend.alive:
            backend.rtt_seconds = (
                elapsed
                if backend.rtt_seconds == 0.0
                else backend.rtt_seconds + 0.2 * (elapsed - backend.rtt_seconds)
            )
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
        its address still routes, and the probe asks it again, so a container that was still loading
        its weights when the gateway started describes itself once it is up.
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

        A backend that never declared anything **is** asked again here, on this same clock: the
        gateway and a container start independently, and the retry belongs to the probe rather than
        to a request, so a page load never waits three seconds on a machine that is switched off.
        """
        for backends in self._backends.values():
            for backend in backends:
                self._probe(backend)
        for model, backends in self._backends.items():
            self._refresh_undeclared(model, backends)

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

        Notes
        -----
        Nothing is asked of a backend here. Describing is what a page does on every load and what a
        predict does before it can even route, so the answer is what the probe last saw — an
        undeclared backend is asked again by :meth:`probe`, on its own clock. Asking from this path
        meant that one switched-off backend made a request wait three seconds, once per retry
        window, on every model that had it.
        """
        backends = self._backends.get(model)
        if backends is None:
            raise HTTPException(status_code=404, detail="no such model")
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
            declared=any(backend.declared for backend in backends),
            parameters=declared.parameters if declared else [],
            machines=[backend.machine for backend in live if backend.machine is not None],
            # The age of the newest look, not the oldest: the probe asks a model's backends in one
            # pass, so what a reader needs to know is when that pass happened.
            checked_seconds_ago=None if not checked else max(0, round(now - checked)),
        )

    def machines(self) -> list[MachineSummary]:
        """Report the pool as machines rather than as models, in name order.

        Returns
        -------
        list of MachineSummary
            One entry per machine any backend runs on, with the models it serves, whether any of
            its containers is answering, the best measured round trip, the depth this gateway has
            against it, and the age of its newest probe.

        Notes
        -----
        Grouping is :attr:`Backend.machine_key`'s, deliberately the same key the router counts and
        scores by: an operator reading `/pool` must see the queue the routing decision used, not a
        second grouping of the same containers that could disagree with it.

        The round trip is the **best** of the machine's containers, because they share its network
        path: the slower figure is a container's own startup, not the route to the card. Nothing is
        asked here — `/pool` reads what the probe last said and must stay cheap enough to poll, and
        asking a backend on the request path is what `gateway.md` rules out.
        """
        now = time.monotonic()
        grouped: dict[str, list[tuple[str, Backend]]] = {}
        for model, backends in self._backends.items():
            for backend in backends:
                grouped.setdefault(backend.machine_key, []).append((model, backend))
        with self._lock:
            depths = dict(self._machine_in_flight)
        machines: list[MachineSummary] = []
        for machine in sorted(grouped):
            pairs = grouped[machine]
            measured = [backend.rtt_seconds for _, backend in pairs if backend.rtt_seconds > 0]
            probed = max((backend.probed_at for _, backend in pairs), default=0.0)
            # The newest failure rather than the worst: what an operator wants is the state of
            # things now, and an old refusal that a later request never repeated is history.
            newest = max(pairs, key=lambda pair: pair[1].last_failure_at)[1]
            failed_at = newest.last_failure_at
            machines.append(
                MachineSummary(
                    machine=machine,
                    models=sorted({model for model, _ in pairs}),
                    alive=any(backend.alive for _, backend in pairs),
                    rtt_ms=min(measured) * 1000.0 if measured else None,
                    in_flight=depths.get(machine, 0),
                    probed_seconds_ago=None if not probed else max(0, round(now - probed)),
                    last_failure=(
                        None
                        if newest.last_failure is None
                        else f"{newest.last_failure} at {newest.last_failure_stage}"
                        if newest.last_failure_stage
                        else newest.last_failure
                    ),
                    failed_seconds_ago=None if not failed_at else max(0, round(now - failed_at)),
                    failures=sum(backend.failures for _, backend in pairs),
                    passed_over=all(backend.is_passed_over(now) for _, backend in pairs),
                )
            )
        return machines

    def _begin(self, model: str, mode: str) -> tuple[list[Backend], int, int]:
        """Order a model's live backends and reserve the one that will be tried first.

        Parameters
        ----------
        model : str
            The model id.
        mode : str
            ``predict`` or ``predict/upload``, which decides what one request already in flight on a
            backend costs this one — the figure the order is scored with.

        Returns
        -------
        backends : list of Backend
            The backends that are answering and not currently passed over, ordered by the wait this
            request would expect (see :meth:`_order`), and the first of them already counted as in
            flight. Empty means the caller should be told the model is unavailable rather than made
            to wait for a machine that is off — and it is the same set the published count is taken
            from, so `0` means the same thing in both places.
        ahead : int
            How many requests were already counted against the first backend *before* this one
            reserved it — the queue depth this attempt joins, and `0` when there are no backends.
        machine_ahead : int
            How many requests were already in flight on the **machine** that backend runs on,
            across every model it serves. This is the queue the order was scored with, and it can be
            larger than `ahead` when one card carries several containers.

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

        The depth is returned rather than stored on the `Backend`, because it describes *this*
        attempt: a field there would be overwritten by the next request before this one logged it.
        Reading it inside the lock is what makes it the true depth at dispatch rather than a number
        another request may have moved in between.
        """
        if model not in self._backends:
            raise HTTPException(status_code=404, detail="no such model")
        ahead = 0
        machine_ahead = 0
        with self._lock:
            backends = self._order(self._backends[model], mode)
            if backends:
                chosen = backends[0]
                key = chosen.machine_key
                ahead = chosen.in_flight
                machine_ahead = self._machine_in_flight.get(key, 0)
                chosen.in_flight += 1
                self._machine_in_flight[key] = machine_ahead + 1
        return backends, ahead, machine_ahead

    def _order(self, backends: Sequence[Backend], mode: str) -> list[Backend]:
        """Order the live backends by the wait the next request would expect, cheapest first.

        Parameters
        ----------
        backends : sequence of Backend
            One model's backends, in the order the host listed them.
        mode : str
            ``predict`` or ``predict/upload``, which decides what one request already in flight
            costs — see :data:`DELAY_PER_QUEUED_REQUEST`. An unknown mode pays the row figure.

        Returns
        -------
        list of Backend
            The same backends, sorted ascending by
            ``rtt_seconds + machine_depth * DELAY_PER_QUEUED_REQUEST[mode]``, where the depth is the
            number of requests in flight on the **machine** the backend runs on, across every model
            it serves. Sorting is stable, so backends scoring the same keep the host's list order —
            which is what reproduces the idle-first order exactly when every round trip and every
            service time are equal.

        Notes
        -----
        The two terms are the same unit, seconds of waiting this request pays: the round trip to
        reach the machine, and the part of a service time the requests already running there will
        use before this one is served. Neither can decide alone. A nearer machine wins while both
        are idle, and the 0.26 s link to the laboratory is the same order as a queue of one — about
        a quarter of a service time, because one container overlaps an image fetch with its GPU
        work — so a backend one request deep is not necessarily the slower choice.

        **The depth is the machine's, not the container's**, and that is the difference a card
        carrying several models makes. A request for the third model of a machine whose other two
        containers are already inferring sees its own container empty and would call the machine
        free; counting per machine is what stops the router stacking a third inference on one card
        while another card is idle. Two containers that declare different machine names are two
        machines for this purpose, which is why every container names the machine it runs on.

        The caller holds :attr:`_lock`: the counts and the order must be read together, or a burst
        would all see the same empty machine.

        Ties on the score are broken by the **container's** own depth rather than by list order,
        because a card carrying two containers should send the next request to the one whose lock is
        free: on the same machine the two score alike, and picking by list order would keep feeding
        the first container while the second sat idle.

        The in-flight term exists because a busy group that kept list order made the first entry the
        pool's only lane: measured against three cards on 2026-10-17, 160 simultaneous calls put 140
        on the first listed backend and 20 on the other two, and the pool answered at one card's
        rate. Idle-first alone did not cover it, because a backend is only known to be busy after a
        request has already been counted against it; the scalar subsumes it.
        """
        now = time.monotonic()
        live = [
            backend for backend in backends if backend.alive and not backend.is_passed_over(now)
        ]
        delay = DELAY_PER_QUEUED_REQUEST.get(mode, 0.25)
        depth = self._machine_in_flight
        return sorted(
            live,
            key=lambda backend: (
                backend.rtt_seconds + depth.get(backend.machine_key, 0) * delay,
                backend.in_flight,
            ),
        )

    def _reserve(self, backend: Backend) -> tuple[int, int]:
        """Count one attempt against a backend, a fallback reached later in the same request.

        Parameters
        ----------
        backend : Backend
            The backend about to be tried.

        Returns
        -------
        ahead : int
            How many requests were already counted against this backend before this reservation.
        machine_ahead : int
            How many were already in flight on its machine, across every model, before it.

        Notes
        -----
        Both figures are read under the same lock that increments them, for the reason
        :meth:`_begin` gives, and both are returned rather than stored on the `Backend`.
        """
        with self._lock:
            key = backend.machine_key
            ahead = backend.in_flight
            machine_ahead = self._machine_in_flight.get(key, 0)
            backend.in_flight += 1
            self._machine_in_flight[key] = machine_ahead + 1
        return ahead, machine_ahead

    def _release(self, backend: Backend) -> None:
        """Stop counting one attempt.

        Always called from a `finally`: an exception on this path must not leave a backend looking
        permanently busy, which would take it out of the pool for the life of the process.

        Notes
        -----
        The machine's count falls with it, and the key is dropped at zero so the dictionary stays as
        large as the machines currently working rather than every machine ever seen. The key is read
        again here rather than carried from the reservation: a machine does not rename itself while
        a request is in flight, and one that did has restarted, which fails the request anyway.
        """
        with self._lock:
            backend.in_flight -= 1
            key = backend.machine_key
            remaining = self._machine_in_flight.get(key, 0) - 1
            if remaining > 0:
                self._machine_in_flight[key] = remaining
            else:
                self._machine_in_flight.pop(key, None)

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

        Notes
        -----
        **One clock covers the whole request, retries included.** The deadline is
        :data:`RESPONSE_TIMEOUT_SECONDS` plus the send allowance a body needs at
        :data:`MIN_SEND_BYTES_PER_SECOND`, and each attempt is given only what is left of it.
        Without that, two dead backends cost two full budgets and a caller waits twice for the same
        answer; with it, a fallback inherits the first attempt's overspend and the request ends when
        its budget does.

        A fallback that times out on the part of the budget it was handed is not marked unhealthy.
        The transport cannot tell "this machine is slow" from "this attempt was given the last two
        seconds of a request", and charging the machine for the request's clock would shut a healthy
        backend out of the pool because another one was slow — the same mistake the send/read split
        exists to avoid. The counter is charged only where the attempt really did have its own full
        budget, and the request still ends `504`.
        """
        if not self._token:
            # Fail closed and say whose fault it is. Without a token every backend would answer 401,
            # which the surface maps to a client error — a misconfigured gateway reported as a bad
            # request, which sends the reader looking in the wrong place. Asked before the
            # reservation: a machine that is never called must not keep a lane counted against it.
            raise HTTPException(
                status_code=503,
                detail="the gateway is not configured to reach models",
            )
        backends, ahead, machine_ahead = self._begin(model, path)
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
        # One budget for the request, send included. A body's send is limited by the link rather
        # than by the machine, so its allowance is added here once; the transport spends that same
        # number across connect, send and read rather than resetting a clock per phase.
        send_allowance = len(body) / MIN_SEND_BYTES_PER_SECOND if body else 0.0
        deadline = time.monotonic() + send_allowance + RESPONSE_TIMEOUT_SECONDS
        for index, backend in enumerate(backends):
            if index:
                if deadline - time.monotonic() <= 0.0:
                    # Every attempt so far has spent what the request had. Asking another machine
                    # would extend a request the caller was promised would end, and it would be
                    # judged on a budget the earlier attempts left rather than on its own work.
                    logger.warning("no budget left to try another backend for %s", model)
                    break
                # A fallback was not reserved with the list, because it was not being tried yet.
                # Its own pre-increment depth is what this attempt queued behind.
                ahead, machine_ahead = self._reserve(backend)
            remaining = deadline - time.monotonic()
            url = f"{backend.url}/models/{model}/{path}{suffix}"
            started = time.monotonic()
            try:
                reply = self._transport("POST", url, headers, body, remaining)
            finally:
                # Stopped before the release so the figure measures the transport call and nothing
                # around it: the lock the release takes is the gateway's time, not the backend's.
                elapsed_ms = (time.monotonic() - started) * 1000.0
                self._release(backend)
            # One line per attempt, immediately after the transport returned, for the operator
            # reading a slow or failing request. Counts and addresses only: the query and both
            # bodies are the participant's content and never appear here.
            logger.info(
                "inference model=%s backend=%s mode=%s attempt=%d ahead=%d machine_ahead=%d "
                "status=%d ms=%.1f bytes_in=%d bytes_out=%d",
                model,
                backend.url,
                path,
                index,
                ahead,
                machine_ahead,
                reply.status,
                elapsed_ms,
                len(body) if body is not None else 0,
                len(reply.body),
            )
            if 200 <= reply.status < 300:
                backend.unhealthy_until = 0.0
                backend.server_errors = 0
                backend.ambiguous_streak = 0
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
                raise HTTPException(
                    status_code=_refusal_status(reply.status),
                    detail=_refusal_detail(reply),
                )
            if reply.timed_out and reply.stage == "send":
                # The body was still going out when the clock ended: that is this host's path to
                # the machine, not the machine. It is neither passed over nor retried — the same
                # body would spend the same send on the next link — so the caller is told what
                # happened and the pool is left alone.
                logger.warning("sending to %s timed out; the link, not the backend", backend.url)
                raise HTTPException(
                    status_code=504,
                    detail="the upload could not be sent to the model in time",
                )
            # The cause is named rather than reduced to a status: `0` covers a refused connection,
            # no route and an unreadable name, and none of them is a timeout — a log line reading
            # "timeout" would send an operator to the wrong knob. The transport logs the operating
            # system's own words one line above.
            kind = _failure_kind(reply)
            cause = f"{kind} at {reply.stage}" if reply.stage else kind
            if reply.timed_out and index > 0:
                # This attempt was handed only the remainder of the request's one clock. A timeout
                # here is the request's budget running out, not a verdict on this machine, so the
                # machine keeps its place and its error count untouched. The next iteration sees an
                # exhausted deadline and stops; the caller still learns the request timed out.
                logger.warning(
                    "backend %s timed out on the budget left by earlier attempts; still serving",
                    backend.url,
                )
                failures.append(reply)
                continue
            if _record_failure(backend, reply, kind, time.monotonic()):
                logger.warning("backend %s failed a %s: %s", backend.url, model, cause)
                backend.unhealthy_until = time.monotonic() + UNHEALTHY_SECONDS
                failures.append(reply)
                continue
            if kind != "status":
                # A first timeout is not a verdict yet — the next machine gets the request, and the
                # pool keeps this one until it either fails again or answers.
                logger.warning(
                    "backend %s failed a %s: %s; not a verdict yet", backend.url, model, cause
                )
                failures.append(reply)
                continue
            # One request the backend could not handle. Retrying it elsewhere would spread whatever
            # it is, so it is answered here and the machine stays in the pool.
            logger.warning(
                "backend %s could not handle a %s (%s, %d in a row); still serving",
                backend.url,
                model,
                cause,
                backend.server_errors,
            )
            raise HTTPException(
                status_code=502,
                detail="the model could not handle this request",
            )

        last = failures[-1]
        if last.timed_out or deadline - time.monotonic() <= 0.0:
            # Either the last attempt itself ended on the clock, or the clock ended the request
            # before another machine could be given a fair try. Both are the same answer to the
            # caller: the budget ran out, and the pool was not charged for it beyond what a
            # full-budget attempt established.
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


def _resolved(
    datasets: Mapping[str, Dataset], dataset: str, row: int, base: str
) -> tuple[str, str]:
    """Resolve an address to the public URL of its image, with the revision it was published at.

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
    url : str
        The public `data.*` URL the backend is told to fetch.
    revision : str
        The dataset's published revision, which is part of a cache key: a republish changes it, and
        the cache goes cold instead of answering about an image that has moved.

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
    return str(image_addresses(found, image, base)["image_url"]), found.revision


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


MAX_QUERY_BYTES = 2048
"""Bytes one query string may carry.

A transport cap, not a parameter's: the whole query is forwarded verbatim to a backend, so an
unbounded one is a cheap way to make the pool carry it. It replaced a 200-character cap on one
parameter name, because the names belong to the model and this package may not know which of them is
the long one. Two kilobytes is about sixty hand-written boxes.
"""

_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
"""What an accepted upload starts with, and the type it is therefore forwarded as.

A magic-byte table rather than the multipart `Content-Type`, because that header is the client's
claim and this is the one decision it must not make. It is also not a validator: the backend decodes
the bytes with Pillow and is the authority on whether they are a usable image. What this stops is
the case that has no possible answer — a script, an archive, or a text file reaching a lane to be
refused there — and it makes the `Content-Type` the backend receives true.

HEIC and camera RAW are deliberately absent: Pillow cannot decode them, so accepting them would only
move the refusal later with a worse message. The refusal says so.
"""


def _image_media_type(payload: bytes) -> str:
    """Name the image format these bytes are, or refuse them.

    Parameters
    ----------
    payload : bytes
        The uploaded body, which the caller has already read.

    Returns
    -------
    str
        The media type of the payload's own header, which is what a backend is told it received.

    Raises
    ------
    HTTPException
        ``400`` when the bytes start with nothing in :data:`_IMAGE_MAGIC`.
    """
    for magic, media_type in _IMAGE_MAGIC:
        if payload.startswith(magic):
            return media_type
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    raise HTTPException(
        status_code=400,
        detail=(
            "the upload is not an image this API accepts; send JPEG, PNG, WebP, TIFF or GIF "
            "(a phone's HEIC or a camera RAW file needs converting to JPEG first)"
        ),
    )


def _refuse(message: str) -> NoReturn:
    """Refuse a request in the contract's own error shape.

    Parameters
    ----------
    message : str
        One line naming the declared parameter and what it accepts, never a value the caller sent.

    Raises
    ------
    HTTPException
        Always, with `400` and a message Swagger shows as the reason.
    """
    raise HTTPException(status_code=400, detail=message)


def _accepted(declared: Mapping[str, ModelParameter]) -> str:
    """Name what a model does accept, for the refusal that lists it."""
    return ", ".join(declared) or "no parameters"


def _as_number(parameter: ModelParameter, value: str) -> float:
    """Read a numeric value, refusing anything that is not a finite number."""
    try:
        number = float(value)
    except ValueError:
        _refuse(f"{parameter.name} must be a number")
    if not math.isfinite(number):
        _refuse(f"{parameter.name} must be a finite number")
    return number


def _check_bounds(parameter: ModelParameter, number: float) -> None:
    """Refuse a number the declaration does not accept, unless the ceiling is the container's.

    A `clamp` ceiling is the one place a value outside the declaration is forwarded: the container
    cannot go above what it was measured at, and answering at the ceiling with a warning is more
    useful than a refusal. Everything below the smallest accepted value is a mistake either way.
    """
    clamped = parameter.above_maximum == "clamp"
    if parameter.values is not None:
        offered = ", ".join(f"{value:g}" for value in parameter.values)
        if number < min(parameter.values):
            _refuse(f"{parameter.name} is one of {offered}")
        if number > max(parameter.values) and not clamped:
            _refuse(f"{parameter.name} is one of {offered}")
        return
    if parameter.minimum is not None and number < parameter.minimum:
        _refuse(f"{parameter.name} must be at least {parameter.minimum:g}")
    if parameter.maximum is not None and number > parameter.maximum and not clamped:
        _refuse(f"{parameter.name} must be at most {parameter.maximum:g}")


def _check_geometry(parameter: ModelParameter, value: str) -> None:
    """Refuse a boxes or points value whose shape is not the one this contract publishes.

    Shape only. A box is four numbers in the image's pixels and an optional label; a point is two
    and an optional label. Whether a coordinate lies inside the image, and what a positive or
    negative label means to a particular model, is the backend's business — it already decoded the
    image, and this surface keeps no image decoder of its own.
    """
    coordinates = 4 if parameter.type == "boxes" else 2
    lengths = (coordinates, coordinates + 1)
    try:
        document = json.loads(value)
    except json.JSONDecodeError:
        _refuse(f"{parameter.name} must be a JSON array of arrays")
    if not isinstance(document, list) or not document:
        _refuse(f"{parameter.name} must be a non-empty JSON array of arrays")
    for element in document:
        if not isinstance(element, list) or len(element) not in lengths:
            _refuse(
                f"each {parameter.name} entry is {coordinates} numbers, "
                f"optionally followed by a 1 or 0 label"
            )
        for coordinate in element[:coordinates]:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                _refuse(f"{parameter.name} coordinates must be numbers")
            if isinstance(coordinate, float) and not math.isfinite(coordinate):
                _refuse(f"{parameter.name} coordinates must be finite numbers")
        if len(element) == coordinates + 1 and element[-1] not in (0, 1):
            _refuse(f"the label on a {parameter.name} entry is 1 for positive or 0 for negative")


def _check_value(parameter: ModelParameter, value: str) -> None:
    """Refuse a value the declaration does not accept, in the parameter's own terms."""
    if parameter.type == "number":
        _check_bounds(parameter, _as_number(parameter, value))
    elif parameter.type == "boolean":
        if value.lower() not in {"true", "false", "1", "0"}:
            _refuse(f"{parameter.name} must be true or false")
    elif parameter.type == "enum":
        offered = ", ".join(parameter.enum or [])
        if value not in (parameter.enum or []):
            _refuse(f"{parameter.name} is one of {offered}")
    elif parameter.type in {"boxes", "points"}:
        _check_geometry(parameter, value)
    elif parameter.max_length is not None and len(value) > parameter.max_length:
        _refuse(f"{parameter.name} must be at most {parameter.max_length} characters")


def validate_query(summary: ModelSummary, pairs: Sequence[tuple[str, str]]) -> None:
    """Refuse a query that the model did not declare, before it reaches a machine.

    Validated against the model's own declaration, which is the only place its contract lives: this
    function knows no parameter name of its own, and a model whose declaration is different is a
    configuration change rather than a code change.

    Parameters
    ----------
    summary : ModelSummary
        The model's entry, as `GET /models/{model}` publishes it. Its :attr:`ModelSummary.declared`
        decides whether there is anything to validate against.
    pairs : sequence of (str, str)
        The query string's items in order, so a repeated name can be told apart from a single one.

    Raises
    ------
    HTTPException
        `400` for a name the model did not declare, a value it does not accept, a parameter sent
        twice, or a combination its declaration rules out.

    Notes
    -----
    A model that has never declared anything is not validated at all. Its parameter list is empty
    because the gateway has not read one, not because the model takes nothing — a container whose
    `/health` answered with an unreadable body is the case — and refusing there would break a call
    that works. `declared` is what keeps those two apart.
    """
    if not summary.declared:
        return
    declared = {parameter.name: parameter for parameter in summary.parameters}
    seen: dict[str, str] = {}
    for name, value in pairs:
        parameter = declared.get(name)
        if parameter is None:
            _refuse(f"{name} is not a parameter; this model accepts {_accepted(declared)}")
        if name in seen:
            _refuse(f"{name} is sent once; a value that is a list is one JSON array")
        seen[name] = value
        _check_value(parameter, value)
    for parameter in summary.parameters:
        if parameter.name not in seen:
            if parameter.required:
                _refuse(f"{parameter.name} is required")
            continue
        for other in parameter.requires:
            if other not in seen:
                _refuse(f"{parameter.name} requires {other}")
        for other in parameter.excludes:
            if other in seen:
                _refuse(f"{parameter.name} and {other} are not accepted together")


def _checked_query(summary: ModelSummary, request: Request) -> str:
    """Validate a request's query and return it, verbatim, for forwarding.

    Parameters
    ----------
    summary : ModelSummary
        The model the request is for, as the directory publishes it.
    request : Request
        The incoming request.

    Returns
    -------
    str
        The raw query string, which is what a backend receives: the values are the model's and this
        package does not decode and re-encode what it has only just validated.

    Raises
    ------
    HTTPException
        `400` when the query is over :data:`MAX_QUERY_BYTES`, or when
        :func:`validate_query` refuses a name, a value, or a combination.
    """
    query = request.url.query
    if len(query.encode("utf-8")) > MAX_QUERY_BYTES:
        _refuse(f"the query string must be at most {MAX_QUERY_BYTES} bytes")
    validate_query(summary, request.query_params.multi_items())
    return query


def _team(request: Request) -> str:
    """Read the team the request gate authenticated, whose lanes this request counts against.

    Parameters
    ----------
    request : Request
        The incoming request, after the gate has run, which is the only way a route is reached.

    Returns
    -------
    str
        The team id the middleware recorded. Empty only where the gate did not run, which no
        registered route allows — the empty name then shares one ceiling with every other request
        that arrived without a token.
    """
    team: str = getattr(request.state, "team_id", "")
    return team


def create_model_router(
    registry: ModelRegistry,
    datasets: Mapping[str, Dataset],
    data_base_url: str,
    cache: ResultCache,
    slots: ConcurrencyGate,
    uploads: UploadQueue,
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
    cache : ResultCache
        Where a row prediction is looked for before a backend is asked, and stored after it
        answers. Uploads never touch it: their image is the caller's, so their answer is not a
        fact about a published row.
    slots : ConcurrencyGate
        The per-token lane ceiling. A row predict takes its lane here rather than in the
        middleware, because only a miss reaches a GPU at all.
    uploads : UploadQueue
        The byte budget every upload waits on. Only ``predict/upload`` touches it: a row carries no
        bytes worth counting, and making one wait for a photograph would be the wrong queue.

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
            "them with their defaults, and omitting one uses the model's default. Asking the same "
            "question about the same published row again is answered from a cache and occupies no "
            "GPU."
        ),
    )
    def predict(
        model: str,
        request: Request,
        body: PredictInput,
    ) -> ModelPrediction:
        """Segment one image named by an address.

        Parameters
        ----------
        model : str
            The model id from the path.
        request : Request
            The incoming request, whose query string is the model's own parameters. They are
            validated against the declaration and then forwarded verbatim.
        body : PredictInput
            The address to resolve.

        Returns
        -------
        ModelPrediction
            The model's own answer, already in this contract's geometry, with `cached` and
            `cached_at` set when it was served from the store rather than computed for this call.

        Raises
        ------
        HTTPException
            No model has that id; the query names something the model does not accept; the address
            does not resolve; the team holds every lane it may; or every backend failed.

        Notes
        -----
        **The lane is taken here, not in the middleware, because the lookup comes first.** An
        answer the cache already holds occupies no GPU, so charging it a lane would refuse a
        participant for "too many inferences" while no machine was busy — the same mistake that
        made a page load cost a lane before `occupies_lane` existed.

        The stored body is the backend's own bytes, so an answer read back and an answer computed
        now go through one parser and one contract check; only `cached` and `cached_at` are added,
        and only on a hit.
        """
        started = time.monotonic()
        summary = registry.describe(model)
        query = _checked_query(summary, request)
        url, revision = _resolved(datasets, body.dataset, body.row, base)
        asked = canonical_request(
            dataset=body.dataset,
            revision=revision,
            row=body.row,
            model=model,
            query=query,
        )
        answer = _replay(cache, asked, cache.get(asked), model)
        if answer is None:
            with slots.hold(_team(request), f"POST {request.url.path}"):
                reply = registry.forward(
                    model,
                    path="predict",
                    query=query,
                    body=json.dumps({"input": {"url": url}}).encode(),
                    content_type="application/json",
                )
                # Parsed before it is stored: a body this contract cannot read is a broken
                # backend, and storing it would hand that 502 to every later caller of the key.
                answer = _prediction(reply, model)
                if reply.status == 200:
                    cache.put(asked, reply.body)
            verdict = "miss"
        else:
            verdict = "hit"
        # One line per predict, and it says which of the two paths answered: the rate of hits is
        # what tells an operator whether the store is earning the disk it occupies.
        logger.info(
            "cache=%s model=%s dataset=%s row=%d ms=%.1f",
            verdict,
            model,
            body.dataset,
            body.row,
            (time.monotonic() - started) * 1000.0,
        )
        return answer

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
            "gateway, and it carries them no further than the model. Its answer is never cached "
            "either — the image is the caller's, so the answer is not a fact about a published row."
        ),
    )
    def predict_upload(
        model: str,
        request: Request,
        file: UploadFile,
    ) -> ModelPrediction:
        """Segment one uploaded image.

        Parameters
        ----------
        model : str
            The model id from the path.
        request : Request
            The incoming request, whose query string is the model's own parameters. They are
            validated against the declaration and then forwarded verbatim.
        file : UploadFile
            The image, temporarily spooled by the multipart parser. Read into payload memory only
            after link admission, forwarded, and never retained as a published object.

        Returns
        -------
        ModelPrediction
            The model's own answer.

        Raises
        ------
        HTTPException
            No model has that id, the query names something the model does not accept, the upload is
            empty, the link stayed busy for the whole wait (`429` with `Retry-After`), or every
            backend failed.

        Notes
        -----
        Deliberately a plain `def`: the transport is a blocking socket call, so FastAPI runs the
        whole handler in its thread pool and an upload is one thread rather than the gateway. As an
        `async def` it made that call on the event loop and held every other route for the round
        trip — measured against the live gateway on 2026-10-17 with six concurrent uploads,
        `/health` answered in 11.08 s and 5.31 s, and in 1.2 ms once they drained. The sibling
        `predict` route is sync for the same reason; reading the body through `file.file` is what
        lets this one be.

        **The bytes are admitted by the parsed size, and the body is read only afterwards.** The
        part's own length is what the multipart parser counted as it spooled — never the client's
        `Content-Length`, which describes what was sent rather than what arrived — so admission can
        reserve it before the payload exists. Reading first and admitting second kept the whole body
        in the heap of every waiting request: at the designed burst, 50 teams holding three uploads
        each would have been 2.25 GB of photographs against a host with 1,936 MiB of RAM. A part
        that carries no count has nothing to admit, so it is refused rather than read to find one.
        The per-token lane is already held by the middleware, as is the slot that covers the parse;
        what is left here is the link, and the release is in a `finally` so a failed read, a refused
        backend, or a crash frees the bytes it held. The `Retry-After` is whole seconds from the
        queue's own estimate, computed once, so it and the message agree.
        """
        summary = registry.describe(model)
        query = _checked_query(summary, request)
        # The parser counted the bytes it wrote, which is the only figure admission can trust: a
        # client's `Content-Length` describes what was sent, not what arrived.
        size = file.size
        if size is None:
            raise HTTPException(status_code=400, detail="the upload has no declared size")
        if size == 0:
            raise HTTPException(status_code=400, detail="the upload is empty")
        team = _team(request)
        try:
            # The middleware already holds this request's lane, so the gate's count includes this
            # one; what the queue orders by is the work that was in flight before it.
            uploads.acquire(team, max(0, slots.in_flight(team) - 1), size)
        except UploadRefused as refused:
            logger.info(
                "upload refused model=%s team=%s bytes=%d retry_after=%d",
                model,
                team,
                size,
                refused.retry_after_seconds,
            )
            raise HTTPException(
                status_code=429,
                detail=str(refused),
                headers={"Retry-After": str(refused.retry_after_seconds)},
            ) from None
        started = time.monotonic()
        # Nothing has crossed until the transport says it did. A refused connection, or a send the
        # clock ended, carried no bytes to the backend; counting the body anyway would book it as an
        # instant transfer, raise the measured rate, and widen admission exactly when the path is at
        # its worst.
        completed = 0
        try:
            try:
                payload = file.file.read()
            except (OSError, ValueError):
                # Losing the server's spool is not an invalid participant image and must not blame
                # the backend either. The caller may retry once the gateway can read uploads again.
                logger.warning("upload unreadable model=%s team=%s bytes=%d", model, team, size)
                raise HTTPException(
                    status_code=503, detail="the upload could not be read"
                ) from None
            if not payload:
                raise HTTPException(status_code=400, detail="the upload is empty")
            media_type = _image_media_type(payload)
            reply = registry.forward(
                model,
                path="predict/upload",
                query=query,
                body=payload,
                content_type=media_type,
            )
            completed = len(payload) + len(reply.body)
        finally:
            uploads.release(team, size, time.monotonic() - started, completed)
        return _prediction(reply, model)

    return router


def _refusal_status(status: int) -> int:
    """Map a backend's refusal into a status this contract publishes.

    Parameters
    ----------
    status : int
        The backend's own `4xx`.

    Returns
    -------
    int
        ``400`` for a refusal that is about the request — including the ``422`` a framework answers
        with, which this contract never does — and ``502`` for the ones that are about how the
        gateway is configured: a backend that does not serve this model, or that refuses the shape
        the gateway sends. A participant reading ``502`` knows to tell the operator rather than to
        edit a request that was already valid here.
    """
    return 400 if status in (400, 422) else 502


def _refusal_detail(reply: Reply) -> str:
    """Word a backend's refusal as this contract's message.

    Parameters
    ----------
    reply : Reply
        The refusal.

    Returns
    -------
    str
        The backend's own words **only** where it is complaining about the request's parameters,
        which is the one case where its sentence is what the participant needs. Otherwise a
        sentence of this contract's: an internal address or a traceback in a backend's body is not
        something a participant should be reading.
    """
    if reply.status in (400, 422):
        return _refusal(reply)
    if reply.status == 404:
        return "the backend does not serve this model"
    return "the backend could not serve this request"


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


def _replay(
    cache: ResultCache, key: str, stored: CachedAnswer | None, model: str
) -> ModelPrediction | None:
    """Return a stored answer as this contract reads it, or `None` when the store cannot help.

    Parameters
    ----------
    cache : ResultCache
        The store the answer came from, so an entry this contract cannot read can be dropped rather
        than served again.
    key : str
        The key the answer was found under.
    stored : CachedAnswer or None
        What the store returned, if anything.
    model : str
        The model id the answer must claim.

    Returns
    -------
    ModelPrediction or None
        The answer with `cached` and `cached_at` set, or `None` where there was nothing stored or
        what was stored is no longer this contract's answer.

    Notes
    -----
    A store outlives the deploy that filled it. A body written under an older shape may stop
    parsing the day a field becomes required, and replaying it would turn one stale row into a
    permanent `502` for that key — nothing clears it but a shell. A miss is the only failure a
    cache is allowed to have, so the entry is dropped and the caller asks a backend.
    """
    if stored is None:
        return None
    try:
        answer = _prediction(Reply(status=200, body=stored.body), model)
    except HTTPException as unreadable:
        logger.warning("dropping a cached answer this contract cannot read: %s", unreadable.detail)
        cache.forget(key)
        return None
    return answer.model_copy(update={"cached": True, "cached_at": stored.computed_at})


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
