"""The pool's read half: what the gateway accepts as a key.

The write half is the ``biohack-gateway tokens`` command. Both live in this package so that the
writer and the reader share one model and one file format rather than two definitions of it, and
both default to the same path: the pool is host state, not checkout state.

A team is not asked to send anything but its token. The pool is inverted once into
``token -> team_id`` because that is the direction a request arrives in, and the id is what the
request log attributes the work to — which is why a token two teams share is reported rather than
silently attributed to whichever one happened to be read last.
"""

from __future__ import annotations

import logging
from pathlib import Path

from biohack_gateway.tokens import DEFAULT_TOKENS_FILE, TokenPool

logger = logging.getLogger(__name__)


class Authenticator:
    """Answers which team a presented key belongs to.

    The pool is read once and remembered, so a token issued after the gateway started takes effect
    on the next restart — the same bargain every other read-into-memory surface here makes. A
    *failure* is deliberately not remembered: a pool the operator has just repaired starts working
    without a restart, at the cost of repeating one log line per request while it is broken.

    Parameters
    ----------
    path : Path, optional
        The pool file, and the host's own by default.
    """

    def __init__(self, path: Path = DEFAULT_TOKENS_FILE) -> None:
        self._path = path
        self._teams: dict[str, str] | None = None

    def team_for(self, token: str) -> str | None:
        """Name the team a presented token belongs to.

        Parameters
        ----------
        token : str
            The value the client presented. An empty string is simply a token nobody holds.

        Returns
        -------
        str or None
            The team id, or ``None`` when the pool does not hold this token.

        Raises
        ------
        TokenPoolError
            When the pool file exists but cannot be read as a pool. The caller decides how to say
            that; it is not the request's fault.
        """
        # Single-worker, synchronous load: two requests cannot interleave inside it, so the lazy
        # fill needs no lock.
        if self._teams is None:
            self._teams = self._read()
        return self._teams.get(token)

    def _read(self) -> dict[str, str]:
        """Invert the pool on disk into tokens to team ids.

        Returns
        -------
        dict of str to str
            One entry per token. A token held by more than one team is kept for the first of them,
            since a key cannot be two teams at once; the collision is logged for the operator.
        """
        pool = TokenPool.load(self._path)
        by_token: dict[str, str] = {}
        for team_id, token in pool.root.items():
            if token in by_token:
                logger.warning(
                    "token pool: %s and %s are issued the same token; %s keeps it",
                    by_token[token],
                    team_id,
                    by_token[token],
                )
                continue
            by_token[token] = team_id
        logger.info("token pool: %d team(s) read from %s", len(by_token), self._path)
        return by_token
