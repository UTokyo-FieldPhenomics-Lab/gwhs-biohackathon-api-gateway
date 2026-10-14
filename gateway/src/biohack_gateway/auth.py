"""The pool's read half: what the gateway accepts as a key.

The write half is the ``biohack-gateway tokens`` command. Both live in this package so that the
writer and the reader share one model and one file format rather than two definitions of it, and
both default to the same path: the pool is host state, not checkout state.

The pool is re-read whenever the file changes, so a token issued, rotated or revoked during an event
applies on the next request. Revocation is the case that matters: a leaked key has to stop working
without a human remembering a command. The dataset index refreshes the other way, by restart, since
a publish is a deliberate act on a large file while a pool is edited mid-event — the two are not
unified on purpose.

An edit that does not parse never displaces a pool that did: the gateway keeps answering with the
last pool that worked, and logs the problem once. A typo in a file this central should not lock
every participant out; with no good pool behind it, the error stands until the file changes.

A team is not asked to send anything but its token. The pool is inverted once per read into
``token -> team_id`` because that is the direction a request arrives in, and the id is what the
request log attributes the work to — which is why a token two teams share is reported rather than
silently attributed to whichever one happened to be read last.
"""

from __future__ import annotations

import logging
from pathlib import Path

from biohack_gateway.tokens import DEFAULT_TOKENS_FILE, TokenPool, TokenPoolError

logger = logging.getLogger(__name__)

FileStamp = tuple[int, int, int]
"""What counts as "the same file": modification time, size, and inode.

The inode earns its place because the pool is written by staging a temporary file and moving it into
place, so every write lands on a new inode even when the other two would coincide.
"""


def _stamp_of(path: Path) -> FileStamp | None:
    """Describe the file as it is now.

    Parameters
    ----------
    path : Path
        The pool file, which need not exist.

    Returns
    -------
    tuple of int or None
        The stamp, or ``None`` when there is no file — itself a state worth noticing, since a
        pool that was removed must stop being honoured.
    """
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return (info.st_mtime_ns, info.st_size, info.st_ino)


class Authenticator:
    """Answers which team a presented key belongs to.

    Holds the last pool that parsed, and the state of the file it came from. One ``stat`` per
    request is the entire cost of not needing a restart, and it is only paid on the
    authenticated path.

    Parameters
    ----------
    path : Path, optional
        The pool file, and the host's own by default.
    """

    def __init__(self, path: Path = DEFAULT_TOKENS_FILE) -> None:
        self._path = path
        self._teams: dict[str, str] | None = None
        self._error: str | None = None
        self._stamp: FileStamp | None = None
        self._checked = False

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
            When no pool has ever parsed, so nobody can be named. The caller decides how to say
            that; it is not the request's fault.
        """
        self._refresh()
        if self._teams is None:
            raise TokenPoolError(self._error or f"{self._path}: the token pool could not be read")
        return self._teams.get(token)

    def _refresh(self) -> None:
        """Re-read the pool when the file has changed since it was last looked at.

        The comparison is against the stamp taken the last time the file was looked at, whether
        or not that look succeeded — which keeps a broken file to one log line instead of one per
        request, while still noticing the edit that repairs it.
        """
        stamp = _stamp_of(self._path)
        if self._checked and stamp == self._stamp:
            return
        self._stamp = stamp
        self._checked = True
        try:
            self._teams = self._read()
        except TokenPoolError as exc:
            self._error = str(exc)
            logger.error("token pool unusable: %s", exc)
        else:
            self._error = None

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
