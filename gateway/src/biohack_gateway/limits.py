"""How many inferences one participant key may have in flight at once.

The gateway is a fixed number of declared GPU lanes — one per backend, each declaring
`max_in_flight` 1 — so the only thing a client can consume is a lane, and the only fair unit to hold
is one *inference* per lane. Without a cap, one team's loop takes every lane and every other team
waits behind it, which is the fairness complaint this cap exists to prevent rather than to measure.

**Only work that reaches a GPU takes a lane.** A dataset listing, a row's detail and the model
directory are in-memory lookups that answer in microseconds and queue behind no GPU; charging one of
them a lane would let a page's own load be refused while no machine was busy, which is the
opposite of what the cap is for. `app.occupies_lane` decides what the middleware charges — an
upload, which carries bytes to a backend — while a row predict takes its lane in the handler, after
the answer has been looked for in the cache: a stored answer occupies no GPU at all.

**The ceiling is a policy number, not the pool's width.** It used to be the sum of the backends'
declared `max_in_flight`, on the argument that a cap which disagreed with the published capacity
would be a second source of truth about it. That argument stops working the moment one machine
carries several models: the published width counts *containers*, and a machine with three models has
three of them on one card, so a derived cap would hand every team a wider allowance whenever a
container was added — a capacity change dressed as a fairness change. The ceiling is now
`DEFAULT_MAX_CONCURRENT_PER_TOKEN`, written down once and changed deliberately, and the two numbers
are free to disagree: the width says what the pool can hold, the cap says what one team may hold.

**The refusal is immediate, not a queue.** A `429` tells a client to slow down and it can act on
that; a request parked behind an invisible queue cannot tell whether it will be served in one second
or never. The gateway therefore holds no waiting list and no timeout of its own — a request either
occupies a slot or is refused.

A host may override the ceiling for a measurement — one key can then carry a load test without
minting a second class of credential — and `0` disables the cap entirely. Both are read once at
startup, so neither can be changed by a request.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

Limit = int | Callable[[], int]
"""A fixed ceiling or an injectable policy provider; the app uses a fixed default."""

DEFAULT_MAX_CONCURRENT_PER_TOKEN = 3
"""How many inferences one key may have in flight when the host sets no override.

Three, because the pool is three cards and one inference per card is what stops a single team's
loop from occupying all of them. It is a fairness figure and it does not move when a machine gains
another model: a card carrying three containers is still one card.

A host overrides the ceiling with `BIOHACK_MAX_CONCURRENT_PER_TOKEN`, and `0` disables the cap
entirely; both are read once at startup, so neither can be changed by a request.
"""


class LaneRefused(RuntimeError):
    """A request asked to hold a lane its team does not have.

    The exception carries the sentence the caller is shown rather than an HTTP status, because the
    two layers that catch it build their answer differently: the middleware returns a response and a
    route's exception is turned into the contract's `429` by the application's handler. What must
    exist once is the *words* and the log line, and both are written where the cap is.
    """


class ConcurrencyGate:
    """Count the requests each team has in flight, and refuse the ones over the ceiling.

    Parameters
    ----------
    limit : int or callable
        The ceiling per team. An injected callable is consulted on every request; the application
        passes a fixed policy number for the life of the process. Zero disables the gate for a
        measurement; an explicitly negative number is a configuration error.

    Notes
    -----
    One lock guards a dictionary of counters, held only for an increment or a decrement — the same
    shape as the pool's own reservation in `models.py`, and for the same reason: a count read before
    a slot is taken would let a burst past the cap, since none of them would see the others yet.
    A team whose counter reaches zero is dropped, so the dictionary is as large as the number of
    teams currently working, not the number of teams ever seen.

    The ceiling is resolved *before* the lock is taken, because the provider reads the pool's own
    lock; nesting the two in the other order would be a deadlock waiting for a caller.
    """

    def __init__(self, limit: Limit) -> None:
        if not callable(limit) and limit < 0:
            raise ValueError(f"a limit of {limit} is neither a cap nor a way to disable one")
        self._limit = limit
        self._lock = threading.Lock()
        self._in_flight: dict[str, int] = {}

    def ceiling(self) -> int:
        """Report the cap in force now.

        Returns
        -------
        int
            The ceiling per team, or `0` where the gate is disabled.
        """
        return self._limit() if callable(self._limit) else self._limit

    def acquire(self, team: str) -> bool:
        """Take one slot for a team.

        Parameters
        ----------
        team : str
            The team the presented token belongs to.

        Returns
        -------
        bool
            ``True`` when the slot was taken and must later be released, or when the gate is
            disabled; ``False`` when the team is already at its ceiling and nothing was counted.
        """
        ceiling = self.ceiling()
        if ceiling <= 0:
            return True
        with self._lock:
            held = self._in_flight.get(team, 0)
            if held >= ceiling:
                return False
            self._in_flight[team] = held + 1
            return True

    @contextmanager
    def hold(self, team: str, what: str) -> Iterator[None]:
        """Hold one of a team's lanes for the block, or refuse it with the cap's own sentence.

        Parameters
        ----------
        team : str
            The team the presented token belongs to.
        what : str
            The request, written for the log line — ``POST /models/x/predict``.

        Yields
        ------
        None
            With the lane counted against `team`.

        Raises
        ------
        LaneRefused
            The team already holds its ceiling; nothing was counted and nothing is released.

        Notes
        -----
        One helper for both routes that take a lane, so the refusal's sentence and its log line are
        written once. It is a context manager rather than an acquire/release pair because the
        release belongs on every path out of the block, including an exception — the mistake this
        shape makes impossible is a lane held for the life of the process.
        """
        if not self.acquire(team):
            logger.info("%s team=%s refused: %d lanes busy", what, team, self.in_flight(team))
            raise LaneRefused(self.refusal())
        try:
            yield
        finally:
            self.release(team)

    def refusal(self) -> str:
        """Write the sentence a caller over its ceiling reads.

        Returns
        -------
        str
            The ceiling in force and what to do about it, with no value the caller sent in it.
        """
        return f"at most {self.ceiling()} inferences at once per token; retry when one finishes"

    def release(self, team: str) -> None:
        """Give one slot back.

        Always called from a `finally`, for the same reason the pool releases a backend that way: a
        counter that only ever rises would refuse a team for the life of the process.

        Parameters
        ----------
        team : str
            The team whose request finished.
        """
        with self._lock:
            held = self._in_flight.get(team, 0) - 1
            if held > 0:
                self._in_flight[team] = held
            else:
                self._in_flight.pop(team, None)

    def in_flight(self, team: str) -> int:
        """Report what one team holds, which is what the tests assert against.

        Parameters
        ----------
        team : str
            The team to look at.

        Returns
        -------
        int
            The number of requests it currently has in flight, `0` where it holds none.
        """
        with self._lock:
            return self._in_flight.get(team, 0)
