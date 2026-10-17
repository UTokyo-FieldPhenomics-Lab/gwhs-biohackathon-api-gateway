"""How many requests one participant key may have in flight at once.

The gateway is a fixed number of serial GPU lanes — one per backend, each declaring
`max_in_flight` 1 — so the only thing a client can consume is a lane, and the only fair unit to hold
is one request per lane. Without a cap, one team's loop takes every lane and every other team waits
behind it, which is the fairness complaint this cap exists to prevent rather than to measure.

Three, because three is the pool's declared width: a team may reach it and never exceed it. The
number is deliberately not a tuning knob — it is the pool's own figure, and a cap that disagreed
with `GET /models` would be a second source of truth about capacity.

**The refusal is immediate, not a queue.** A `429` tells a client to slow down and it can act on
that; a request parked behind an invisible queue cannot tell whether it will be served in one second
or never. The gateway therefore holds no waiting list and no timeout of its own — a request either
occupies a slot or is refused.
"""

from __future__ import annotations

import threading

MAX_CONCURRENT_PER_TOKEN = 3
"""Requests one key may have in flight at once: the pool's width, not a preference."""


class ConcurrencyGate:
    """Count the requests each team has in flight, and refuse the ones over the cap.

    Parameters
    ----------
    limit : int, optional
        The cap per team. Defaults to :data:`MAX_CONCURRENT_PER_TOKEN`.

    Notes
    -----
    One lock guards a dictionary of counters, held only for an increment or a decrement — the same
    shape as the pool's own reservation in `models.py`, and for the same reason: a count read before
    a slot is taken would let a burst past the cap, since none of them would see the others yet.
    A team whose counter reaches zero is dropped, so the dictionary is as large as the number of
    teams currently working, not the number of teams ever seen.
    """

    def __init__(self, limit: int = MAX_CONCURRENT_PER_TOKEN) -> None:
        if limit < 1:
            raise ValueError(f"a limit of {limit} refuses every request")
        self._limit = limit
        self._lock = threading.Lock()
        self._in_flight: dict[str, int] = {}

    def acquire(self, team: str) -> bool:
        """Take one slot for a team.

        Parameters
        ----------
        team : str
            The team the presented token belongs to.

        Returns
        -------
        bool
            ``True`` when the slot was taken and must later be released, ``False`` when the team is
            already at its cap and nothing was counted.
        """
        with self._lock:
            held = self._in_flight.get(team, 0)
            if held >= self._limit:
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
