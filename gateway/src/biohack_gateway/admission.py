"""Bytes are what an upload costs, so bytes are what admission counts.

A row request crosses this gateway as a few hundred bytes of JSON in each direction. An upload
sends up to fifteen megabytes out and brings an answer back, and every upload shares one link to the
pool. The measurement this module exists for is that the link — not the card — is what saturates
first: one stream to the laboratory ran at 1.55 MB/s while the card answered in a tenth of a second,
and twelve streams together reached only about 2.2 MB/s. Fifty participants each sending a 10 MB
photograph would put half a gigabyte on that link at once: minutes of transfer, and nothing to show
for any of it until the queue drains.

**The budget is in bytes, and its size is measured.** A lane (`limits.ConcurrencyGate`) is a
fairness figure fixed by policy; how many bytes the link can carry is a fact about the network at
this moment, so :class:`LinkRate` estimates it from completed uploads and the budget follows it. A
bad night tightens admission by itself, without anybody deciding to change a number.

**Admission is a queue with a deadline, not a refusal.** A participant parking an upload for a few
seconds is better served than one told "busy" with no idea when to come back — but only up to a
point: past :data:`DEFAULT_WAIT_SECONDS` the request is refused as `429` with a `Retry-After` that
carries the estimate, so a client can decide instead of guessing.

**Only uploads are admitted.** Fifty concurrent row requests are absorbed by the thread pool and the
cache, and a row carries no bytes worth counting; putting them behind this queue would make a
metadata-shaped request wait for a photograph. `predict` never touches this module.

**A slot is the other resource an upload holds, and it is taken before the bytes exist.** The byte
queue can only admit what it can measure, and a body's length is known only after the multipart
parser has spooled it — up to fifteen megabytes, in memory or on the tmpfs — which happens before
the handler runs. So a second gate counts uploads from parse to answer (:class:`UploadSlots`): the
middleware takes one slot before the body is parsed and gives it back when the handler returns, and
a request that finds none is refused at once rather than parked. Eight slots is 120 MB of spooled
bodies and at most 120 MB of payload, the reserve written down at :data:`DEFAULT_UPLOAD_SLOTS`.

The queue is ordered by ``(team_in_flight, arrival)``. That is the whole fairness rule: of the
requests that can run now, the one whose team is using the link least goes first, and ties are
broken by who asked first. A team cannot overtake itself out of order, and a team that has nothing
in flight cannot be starved by one holding several uploads.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_BYTES_PER_SECOND = 1_500_000.0
"""What the link is assumed to carry until it is measured: one stream's 1.55 MB/s, rounded down.

The seed is deliberately the figure of *one* upload rather than the aggregate: admission that starts
too wide lets a burst pile onto the link before the estimate has anything to say, and the first few
uploads are what teach it the truth.
"""

MIN_BYTES_PER_SECOND = 200_000.0
"""The floor of the estimate, so a stalled sample cannot shrink the budget to nothing.

A link that really is this slow has a budget of 2.4 MB, smaller than one photograph; the empty-link
rule in :meth:`UploadQueue.acquire` is what still lets that photograph through, one at a time.
"""

MAX_BYTES_PER_SECOND = 6_000_000.0
"""The ceiling of the estimate, so one lucky fast transfer cannot open the gate.

It is several times the aggregate a working link showed, and it only bounds an obviously impossible
sample rather than a good one.
"""

WINDOW_SECONDS = 60.0
"""How far back the estimate looks.

Long enough to cover a handful of uploads at a few per minute, short enough that a link which got
better this afternoon is believed this afternoon.
"""

BUDGET_SECONDS = 12.0
"""How many seconds of the link's capacity one round of uploads may occupy.

About one large upload's transfer time at the measured rate, so a single 15 MB body is admitted
while two of them are not. It is this second multiplier, and not the byte cap, that makes a slower
link admit less.
"""

MAX_BUDGET_BYTES = 24_000_000
"""The cap on the budget, whatever the estimate says: two of the largest uploads, at most.

The edge refuses a body over 15 MB (`deploy/Caddyfile`), so a budget above this cannot be used by
anything a participant can send.
"""

DEFAULT_WAIT_SECONDS = 20.0
"""How long an upload may wait before it is refused with a `Retry-After`.

Long enough to ride out a burst — the drain of a dozen 1 MB uploads at the measured rate — and short
enough that a participant's own HTTP client is still interested in the answer. Past it, the explicit
refusal is more useful than an invisible queue.
"""

UPLOAD_BYTES_ENV = "BIOHACK_UPLOAD_BYTES"
"""Overrides :data:`MAX_BUDGET_BYTES`, for a host whose link or edge limit is not this one."""

UPLOAD_WAIT_ENV = "BIOHACK_UPLOAD_WAIT_SECONDS"
"""Overrides :data:`DEFAULT_WAIT_SECONDS`, so a load test can reach the refusal without waiting."""

DEFAULT_UPLOAD_SLOTS = 8
"""How many uploads may be parsed or served at once when the host sets no override.

The number is a capacity calculation, not a taste. A slot is held from the multipart parse until
the handler has answered, so together the slots bound two copies of every body in flight: the
spool, which lives in memory up to a megabyte and then on `/tmp`, and the payload the handler
reads into the heap. At the edge's 15 MB ceiling eight slots hold at most 120 MB of spools and
120 MB of payloads — 240 MB against the host this serves, which has 1,936 MiB of RAM with no swap.
In practice the payloads are far smaller, because the byte queue admits about 24 MB of them at
once (:data:`MAX_BUDGET_BYTES`). The 240 MB bound covers the two stored body copies, not parser
buffers, parsed predictions or total process RSS; those need separate headroom. Each upstream
answer is also bounded by the transport's response ceiling. The pools are per process, which is the
host: `deploy/` runs one worker, so a second worker would double every figure here. A burst deeper
than the slots is refused immediately, so the threads those uploads would have parked never leave
the worker pool.
"""

UPLOAD_SLOTS_ENV = "BIOHACK_UPLOAD_SLOTS"
"""Overrides :data:`DEFAULT_UPLOAD_SLOTS`, for a host whose memory or edge limit is not this one."""

SLOT_RETRY_SECONDS = 5.0
"""What an upload refused for want of a slot is told to wait.

Coarse on purpose: a slot frees when another participant's upload finishes, and nothing here can
know whether that upload is still transferring or already waiting on a GPU. Five seconds is long
enough that an immediate retry is not another refusal and short enough that a client which waits it
out is not punished for an estimate that could only have been a guess.
"""


class UploadRefused(RuntimeError):
    """An upload was not admitted, and how long until trying again is worth it.

    Raised by :meth:`UploadQueue.acquire` when the link stays busy past its deadline, and by
    :meth:`UploadSlots.acquire` when every slot is taken. The sentence names the resource that was
    full rather than an HTTP status, because the layers that catch it are two — a route and the
    middleware — and they build the contract's `429` differently. What must exist once is the words
    and the one number they and `Retry-After` agree on, and both are written here.

    Attributes
    ----------
    retry_after : float
        Seconds until the resource is expected to have room.
    retry_after_seconds : int
        :attr:`retry_after` rounded up to whole seconds, at least one — `Retry-After` is an integer
        number of seconds, and `0` would tell a client to try again immediately.
    """

    def __init__(self, retry_after: float, reason: str) -> None:
        """Write the refusal from the estimate.

        Parameters
        ----------
        retry_after : float
            Seconds until room is expected. It must be finite and positive; the caller gets one
            second where it is not, because a refusal still has to say when to come back.
        reason : str
            Which resource was full, written for the caller — the link or the slot pool. It must not
            carry a value the caller sent.
        """
        self.retry_after = retry_after if math.isfinite(retry_after) and retry_after > 0 else 1.0
        self.retry_after_seconds = max(1, math.ceil(self.retry_after))
        super().__init__(f"{reason}; retry in {self.retry_after_seconds} s")


class UploadSlots:
    """Count the uploads being parsed or served, and refuse one that finds the pool full.

    Parameters
    ----------
    capacity : int or None, optional
        How many uploads may exist at once. `None` reads :data:`UPLOAD_SLOTS_ENV`, or
        :data:`DEFAULT_UPLOAD_SLOTS` where that is unset, is not a number, or is not positive.

    Raises
    ------
    ValueError
        `capacity` was passed explicitly and is not positive — a pool of no slots would refuse every
        upload, which is a host configuration mistake rather than a way to serve.

    Notes
    -----
    **Refusal, not a queue.** :meth:`acquire` never waits: a participant told "busy" in a
    millisecond can decide to retry later, while one parked behind an invisible pool has no way to
    know whether the wait is two seconds or two minutes — and a parked request is a worker thread
    held, which is the resource the slots exist to spare.

    The count is held from before the multipart body is parsed until after the handler has answered,
    because both copies of the body live inside that span. The slot pool and the byte queue are
    separate on purpose: the queue is fair between teams and follows a measured link, while this is
    a flat bound on this host's memory and has nothing to be fair about. A team at its lane ceiling
    is still refused by the lane, which is the fairness number.
    """

    def __init__(self, capacity: int | None = None) -> None:
        resolved = _env_slots() if capacity is None else capacity
        if resolved <= 0:
            raise ValueError(f"{resolved} upload slots is not a capacity")
        self._capacity = resolved
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def capacity(self) -> int:
        """Answer how many uploads may exist at once, for the log line a refusal writes.

        Returns
        -------
        int
            The capacity in force, fixed for the life of the process.
        """
        return self._capacity

    def acquire(self) -> None:
        """Take one slot, or refuse at once.

        Raises
        ------
        UploadRefused
            Every slot is held, carrying :data:`SLOT_RETRY_SECONDS` as the wait and nothing counted.
        """
        with self._lock:
            taken = self._in_flight < self._capacity
            if taken:
                self._in_flight += 1
        if not taken:
            raise UploadRefused(
                SLOT_RETRY_SECONDS, "every upload slot is busy with another participant's image"
            )

    def release(self) -> None:
        """Give one slot back.

        Called from a `finally`, so an upload that failed, was refused by the route, or crashed
        still frees its place: a count that only rose would close the pool for the life of the
        process, exactly as it would for the link's bytes.
        """
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def in_flight(self) -> int:
        """Report how many slots are held, which is what the tests and the log line assert against.

        Returns
        -------
        int
            The number of uploads currently parsed or being served.
        """
        with self._lock:
            return self._in_flight


@dataclass(frozen=True, slots=True)
class QueueStats:
    """What the upload queue is holding, for the operator's surface.

    Attributes
    ----------
    bytes_in_flight : int
        Bytes admitted and not yet released: what the next request has to fit beside.
    budget_bytes : int
        The budget in force now, which moves with :attr:`LinkRate.bytes_per_second`.
    waiting : int
        Requests parked for the link. Rows are never among them.
    bytes_per_team : mapping of str to int
        In-flight bytes per team, so an operator can see one team holding the link.
    """

    bytes_in_flight: int
    budget_bytes: int
    waiting: int
    bytes_per_team: Mapping[str, int]


@dataclass(slots=True, eq=False)
class _Waiter:
    """One request parked for the link, in the order the queue will consider it.

    Identity, not value, is what removes it from the list: two requests of the same team and the
    same size are still two requests, and a value-based removal would take the wrong one out.
    """

    priority: int
    """That team's uploads already in flight when this one arrived; the lower, the sooner."""

    sequence: int
    """Arrival order, which breaks a tie between teams using the link equally."""

    team: str
    size: int


class LinkRate:
    """Estimate what the link carries, from uploads that have finished.

    A sample is one completed upload: its duration and the bytes that crossed in it. The estimate is
    ``bytes / busy seconds`` over the last :data:`WINDOW_SECONDS`, where the busy seconds are the
    **union** of the samples' spans — two uploads in flight together cost one second of the link,
    not two, and counting them twice would underestimate the rate exactly when the link is working
    hardest. A sample that straddles the start of the window contributes its bytes in proportion to
    the part of it inside, which is the only assumption available: nothing here can see inside a
    transfer.

    Parameters
    ----------
    seed : float, optional
        Bytes per second assumed while no sample lies in the window. It is clamped with every other
        figure, so a seed outside the bounds is still a usable estimate.
    window : float, optional
        Seconds the estimate looks back.
    clock : callable, optional
        A monotonic clock in seconds. Injectable so a test can age samples without sleeping.

    Raises
    ------
    ValueError
        `seed` is not positive, or `window` is not positive — either leaves an estimate that cannot
        be computed.

    Notes
    -----
    One lock guards the samples: the predict routes run in FastAPI's thread pool, so two releases
    really do record at once. It is held only while appending, pruning and reading the short deque,
    and it is never taken while the upload queue's own condition is held by another thread in the
    opposite order (the queue takes this lock while holding its own, and this class takes nothing).
    """

    def __init__(
        self,
        *,
        seed: float = DEFAULT_BYTES_PER_SECOND,
        window: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if seed <= 0:
            raise ValueError(f"a link rate of {seed} bytes/s is not a rate")
        if window <= 0:
            raise ValueError(f"a window of {window} s looks at nothing")
        self._seed = seed
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: deque[tuple[float, float, int]] = deque()
        """``(finished_at, seconds, bytes)`` per completed upload, oldest first."""

    def record(self, *, seconds: float, completed_bytes: int) -> None:
        """Add one completed upload to the estimate.

        Parameters
        ----------
        seconds : float
            How long the upload occupied the link. Zero or negative is ignored rather than dividing
            by it: a clock that went backwards says nothing about the link.
        completed_bytes : int
            Bytes that crossed in both directions for that upload.
        """
        if seconds <= 0 or completed_bytes <= 0:
            return
        now = self._clock()
        with self._lock:
            self._samples.append((now, seconds, completed_bytes))
            self._expire(now)

    @property
    def bytes_per_second(self) -> float:
        """Answer the link's capacity as it is measured now, in bytes per second.

        Returns
        -------
        float
            ``completed bytes / busy seconds`` over the window, clamped to
            [:data:`MIN_BYTES_PER_SECOND`, :data:`MAX_BYTES_PER_SECOND`]. The seed answers while no
            sample lies in the window, which is also what a link that has been idle for a minute
            gets: the last figure measured is not evidence about this minute.
        """
        now = self._clock()
        with self._lock:
            self._expire(now)
            return self._estimate(now)

    def _expire(self, now: float) -> None:
        """Drop samples whose span ended before the window starts."""
        oldest = now - self._window
        while self._samples and self._samples[0][0] <= oldest:
            self._samples.popleft()

    def _estimate(self, now: float) -> float:
        """Compute the estimate from the samples currently held.

        Parameters
        ----------
        now : float
            The clock the window is measured from.

        Returns
        -------
        float
            The clamped estimate, or the seed where the window holds no busy second.
        """
        window_start = now - self._window
        total_bytes = 0.0
        spans: list[tuple[float, float]] = []
        for finished_at, seconds, completed in self._samples:
            started_at = max(finished_at - seconds, window_start)
            if started_at >= finished_at:
                continue
            total_bytes += completed * (finished_at - started_at) / seconds
            spans.append((started_at, finished_at))
        busy = _union(spans)
        if busy <= 0 or total_bytes <= 0:
            return _clamp(self._seed)
        return _clamp(total_bytes / busy)


class UploadQueue:
    """Hold uploads until the link has room, in an order that keeps one team from taking it.

    Parameters
    ----------
    link : LinkRate or None, optional
        The estimate the budget follows. `None` builds one with the default seed and the real clock.
    max_budget_bytes : int or None, optional
        The cap on the byte budget. `None` reads :data:`UPLOAD_BYTES_ENV`, or
        :data:`MAX_BUDGET_BYTES` where that is unset.
    budget_seconds : float, optional
        Seconds of the link's capacity a full budget represents.
    wait_seconds : float or None, optional
        How long a request may wait. `None` reads :data:`UPLOAD_WAIT_ENV`, or
        :data:`DEFAULT_WAIT_SECONDS` where that is unset.
    clock : callable, optional
        A monotonic clock in seconds, injectable so a test can reach a deadline without sleeping.
    when_waiting : callable or None, optional
        Called with nothing, under the queue's lock, each time a request is queued for the link. It
        is a seam for tests that need to know a waiter has arrived before releasing the link;
        production passes nothing.

    Raises
    ------
    ValueError
        The cap is not positive, the wait is negative, or `budget_seconds` is not positive — none
        of them leaves a queue that can admit anything.

    Notes
    -----
    **A request larger than the whole budget is admitted to an empty link.** Without that rule a
    15 MB body and a budget of 2.4 MB — a bad night at 0.2 MB/s — would wait forever: nothing it
    waits behind would ever make the budget big enough. The edge's own 15 MB limit is the ceiling on
    one body, so what the queue has to protect is the *sum*, and one request alone is not a sum.

    **The head is considered, never the best fit.** A smaller request behind a larger one waits its
    turn, because "who fits" would let a stream of small uploads starve the large one indefinitely,
    which is the same unfairness this queue exists to prevent in the other direction.

    **Every waiter is woken and one is admitted.** `Condition.notify` wakes an arbitrary thread —
    Python promises neither the longest waiter nor this queue's head — and a woken non-head would
    go back to sleep with the head still waiting, which is a lost wakeup. So a release notifies all
    of them and each re-checks whether it is the head; exactly one fits. The wakeup is cheap
    because the list is as long as the burst, not as long as the afternoon.
    """

    def __init__(
        self,
        link: LinkRate | None = None,
        *,
        max_budget_bytes: int | None = None,
        budget_seconds: float = BUDGET_SECONDS,
        wait_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        when_waiting: Callable[[], None] | None = None,
    ) -> None:
        self._link = link if link is not None else LinkRate(clock=clock)
        self._max_budget_bytes = _env_max_budget() if max_budget_bytes is None else max_budget_bytes
        if self._max_budget_bytes <= 0:
            raise ValueError(f"a budget of {self._max_budget_bytes} bytes can admit nothing")
        if budget_seconds <= 0:
            raise ValueError(f"{budget_seconds} s of the link's capacity is not a budget")
        self._budget_seconds = budget_seconds
        self._wait_seconds = _env_wait() if wait_seconds is None else wait_seconds
        if self._wait_seconds < 0:
            raise ValueError(f"a wait of {self._wait_seconds} s is not a wait")
        self._clock = clock
        self._when_waiting = when_waiting
        self._condition = threading.Condition()
        self._waiters: list[_Waiter] = []
        self._sequence = 0
        self._bytes_in_flight = 0
        self._bytes_per_team: dict[str, int] = {}

    @property
    def budget_bytes(self) -> int:
        """Answer the budget in force now, in bytes.

        Returns
        -------
        int
            ``min(max_budget_bytes, LinkRate.bytes_per_second * budget_seconds)``, truncated to a
            whole number of bytes.
        """
        return int(min(self._max_budget_bytes, self._link.bytes_per_second * self._budget_seconds))

    @property
    def link(self) -> LinkRate:
        """Answer the estimate this queue admits against, for an operator reading `/pool`."""
        return self._link

    def acquire(self, team: str, team_in_flight: int, size: int) -> bool:
        """Wait for room for one upload's bytes, then count them.

        Parameters
        ----------
        team : str
            The team the presented token belongs to; the fairness key.
        team_in_flight : int
            How many uploads that team already has in flight. Read before this request waits, it
            is the fairness key: the team using the link least goes first. It does not count this
            request.
        size : int
            The body's length in bytes.

        Returns
        -------
        bool
            ``True`` once the bytes are counted; the caller must call :meth:`release` on every path
            out of the request.

        Raises
        ------
        UploadRefused
            The link did not make room within the wait, carrying the estimated time until it does.
        ValueError
            `size` is not positive, which is a caller that never read its body.

        Notes
        -----
        The fit is checked before the deadline, so a request that fits is always admitted even when
        the wait is zero — which is how a test and a host that switched waiting off both behave.
        """
        if size <= 0:
            raise ValueError(f"an upload of {size} bytes cannot be admitted")
        with self._condition:
            waiter = _Waiter(priority=team_in_flight, sequence=self._sequence, team=team, size=size)
            self._sequence += 1
            self._waiters.append(waiter)
            self._waiters.sort(key=lambda parked: (parked.priority, parked.sequence))
            if self._when_waiting is not None:
                self._when_waiting()
            deadline = self._clock() + self._wait_seconds
            try:
                while True:
                    if self._admissible(waiter):
                        self._count(waiter.team, waiter.size)
                        return True
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise UploadRefused(
                            self._estimate(waiter),
                            "the upload link is busy with other participants' images",
                        )
                    self._condition.wait(remaining)
            finally:
                # One removal for every path out: admitted, timed out, or interrupted. A waiter left
                # in the list would be the head forever and hold the link for the process's life.
                if waiter in self._waiters:
                    self._waiters.remove(waiter)

    def release(self, team: str, size: int, seconds: float, completed_bytes: int) -> None:
        """Give one upload's bytes back and feed the estimate with what crossed.

        Parameters
        ----------
        team : str
            The team whose upload finished.
        size : int
            The bytes that were counted at admission.
        seconds : float
            How long the upload occupied the link, for the estimate.
        completed_bytes : int
            What crossed in both directions — the body out and the answer back — which is the figure
            the link actually carried.

        Notes
        -----
        Called from a `finally`, so a backend that failed still frees the link: a count that only
        rose would refuse every later upload for the life of the process.
        """
        with self._condition:
            self._bytes_in_flight = max(0, self._bytes_in_flight - size)
            remaining = self._bytes_per_team.get(team, 0) - size
            if remaining > 0:
                self._bytes_per_team[team] = remaining
            else:
                self._bytes_per_team.pop(team, None)
            self._link.record(seconds=seconds, completed_bytes=completed_bytes)
            self._condition.notify_all()

    def stats(self) -> QueueStats:
        """Report what the queue holds, for `/pool`.

        Returns
        -------
        QueueStats
            In-flight bytes, the budget in force, how many requests are waiting, and the in-flight
            bytes per team.
        """
        with self._condition:
            return QueueStats(
                bytes_in_flight=self._bytes_in_flight,
                budget_bytes=self.budget_bytes,
                waiting=len(self._waiters),
                bytes_per_team=dict(self._bytes_per_team),
            )

    def in_flight(self, team: str) -> int:
        """Report the bytes one team has in flight, which is what the tests assert against.

        Parameters
        ----------
        team : str
            The team to look at.

        Returns
        -------
        int
            Its admitted bytes, `0` where it holds none.
        """
        with self._condition:
            return self._bytes_per_team.get(team, 0)

    def _admissible(self, waiter: _Waiter) -> bool:
        """Say whether this waiter may take the link now.

        Parameters
        ----------
        waiter : _Waiter
            The request under consideration.

        Returns
        -------
        bool
            `True` only where it is the head and the link has room for it, or the link is empty and
            it is the head — a body larger than the whole budget is still one upload.
        """
        if not self._waiters or self._waiters[0] is not waiter:
            return False
        if self._bytes_in_flight == 0:
            return True
        return self._bytes_in_flight + waiter.size <= self.budget_bytes

    def _count(self, team: str, size: int) -> None:
        """Add one admitted body to both counters."""
        self._bytes_in_flight += size
        self._bytes_per_team[team] = self._bytes_per_team.get(team, 0) + size

    def _estimate(self, waiter: _Waiter) -> float:
        """Estimate how long the link needs before this request could be served.

        Parameters
        ----------
        waiter : _Waiter
            The request that timed out. Callers hold the queue's lock.

        Returns
        -------
        float
            Seconds to move everything ahead of it — in flight plus every waiter before it — and
            its own body, at the rate the link is measured at. No rate is below
            :data:`MIN_BYTES_PER_SECOND`, so the answer is always finite.
        """
        ahead = self._bytes_in_flight
        for parked in self._waiters:
            if parked is waiter:
                break
            ahead += parked.size
        return (ahead + waiter.size) / self._link.bytes_per_second


def _clamp(rate: float) -> float:
    """Bound an estimate, so admission cannot be opened or closed by one sample.

    Parameters
    ----------
    rate : float
        Bytes per second.

    Returns
    -------
    float
        The same figure between :data:`MIN_BYTES_PER_SECOND` and :data:`MAX_BYTES_PER_SECOND`.
    """
    return min(MAX_BYTES_PER_SECOND, max(MIN_BYTES_PER_SECOND, rate))


def _union(spans: Sequence[tuple[float, float]]) -> float:
    """Measure the part of the clock covered by at least one span.

    Parameters
    ----------
    spans : sequence of (float, float)
        ``(start, end)`` pairs, in any order. Empty means nothing was busy.

    Returns
    -------
    float
        Total seconds covered by at least one span, so two overlapping uploads count one busy second
        each time rather than two.
    """
    if not spans:
        return 0.0
    ordered = sorted(spans)
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    return total + (current_end - current_start)


def _env_max_budget() -> int:
    """Read the byte cap from :data:`UPLOAD_BYTES_ENV`, falling back to the default.

    Returns
    -------
    int
        The configured cap, or :data:`MAX_BUDGET_BYTES` when the variable is unset, is not a
        number, or is not positive — a host with a typo serves with the default rather than
        admitting nothing.
    """
    raw = os.environ.get(UPLOAD_BYTES_ENV, "").strip()
    if not raw:
        return MAX_BUDGET_BYTES
    try:
        cap = int(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %d", UPLOAD_BYTES_ENV, raw, MAX_BUDGET_BYTES)
        return MAX_BUDGET_BYTES
    if cap <= 0:
        logger.warning("%s=%r is not positive; using %d", UPLOAD_BYTES_ENV, raw, MAX_BUDGET_BYTES)
        return MAX_BUDGET_BYTES
    return cap


def _env_slots() -> int:
    """Read the slot capacity from :data:`UPLOAD_SLOTS_ENV`, falling back to the default.

    Returns
    -------
    int
        The configured capacity, or :data:`DEFAULT_UPLOAD_SLOTS` when the variable is unset, is not
        a number, or is not positive — a host with a typo serves with the shipped reserve rather
        than admitting nothing.
    """
    raw = os.environ.get(UPLOAD_SLOTS_ENV, "").strip()
    if not raw:
        return DEFAULT_UPLOAD_SLOTS
    try:
        slots = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using %d", UPLOAD_SLOTS_ENV, raw, DEFAULT_UPLOAD_SLOTS
        )
        return DEFAULT_UPLOAD_SLOTS
    if slots <= 0:
        logger.warning(
            "%s=%r is not positive; using %d", UPLOAD_SLOTS_ENV, raw, DEFAULT_UPLOAD_SLOTS
        )
        return DEFAULT_UPLOAD_SLOTS
    return slots


def _env_wait() -> float:
    """Read the wait from :data:`UPLOAD_WAIT_ENV`, falling back to the default.

    Returns
    -------
    float
        The configured wait, or :data:`DEFAULT_WAIT_SECONDS` when the variable is unset, is not a
        number, or is negative. Zero is a usable value: it makes admission a refusal rather than a
        wait, which is what a load test measuring the tail wants.
    """
    raw = os.environ.get(UPLOAD_WAIT_ENV, "").strip()
    if not raw:
        return DEFAULT_WAIT_SECONDS
    try:
        wait = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using %s", UPLOAD_WAIT_ENV, raw, DEFAULT_WAIT_SECONDS
        )
        return DEFAULT_WAIT_SECONDS
    if wait < 0:
        logger.warning("%s=%r is negative; using %s", UPLOAD_WAIT_ENV, raw, DEFAULT_WAIT_SECONDS)
        return DEFAULT_WAIT_SECONDS
    return wait
