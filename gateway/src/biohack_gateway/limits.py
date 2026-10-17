"""How many requests one participant key may have in flight at once.

The gateway is a fixed number of serial GPU lanes — one per backend, each declaring
`max_in_flight` 1 — so the only thing a client can consume is a lane, and the only fair unit to hold
is one request per lane. Without a cap, one team's loop takes every lane and every other team waits
behind it, which is the fairness complaint this cap exists to prevent rather than to measure.

**The ceiling is the pool's declared width**, read from the same declarations `GET /models` sums,
so a cap that disagreed with the published capacity would be a second source of truth about it. On
three backends that is three. It follows the pool rather than being written down twice: a fourth
card raises it, and a stopped backend lowers it.

**The refusal is immediate, not a queue.** A `429` tells a client to slow down and it can act on
that; a request parked behind an invisible queue cannot tell whether it will be served in one second
or never. The gateway therefore holds no waiting list and no timeout of its own — a request either
occupies a slot or is refused.

A host may override the ceiling for a measurement — one key can then carry a load test without
minting a second class of credential — and `0` disables the cap entirely. Both are read once at
startup, so neither can be changed by a request.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

Limit = int | Callable[[], int]
"""A fixed ceiling, or one read at each request from whoever knows the pool's width."""


class ConcurrencyGate:
    """Count the requests each team has in flight, and refuse the ones over the ceiling.

    Parameters
    ----------
    limit : int or callable
        The ceiling per team. A callable is consulted on every request, which is how the cap
        follows the pool as backends come up and go down; a number is fixed for the life of the
        process. Zero or less disables the gate entirely, which is deliberate rather than
        defensive: a load test needs one key to carry more than the pool's width.

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
