"""The participant token pool.

One flat mapping from team id to bearer token: written by the ``biohack-gateway tokens`` command
(:mod:`biohack_gateway.cli`) and read by the gateway when it starts. It is the one artifact in this
repository that is authoritative on the host rather than kept off it, so the file is written
atomically — a reader must never see half a pool, which would answer some participants and reject
others for no reason they could observe.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from pydantic import RootModel, ValidationError, field_validator

DEFAULT_TOKENS_FILE = Path.home() / ".config" / "biohack-gateway" / "tokens.json"
"""Where the host keeps the pool, and the one path the command defaults to."""

TEAM_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""Team ids reach log lines, URLs and shells, so the set that survives all three is narrow."""

TOKEN_BYTES = 32
"""Entropy per generated token, before base64url encoding."""


class TokenPoolError(Exception):
    """Raised when a pool file cannot be read, or an operation would break it."""


def _check_team_id(team_id: str) -> None:
    """Reject an id that would be ambiguous in a log line, a URL, or a shell.

    Parameters
    ----------
    team_id : str
        The candidate id.

    Raises
    ------
    ValueError
        When the id is empty or holds anything outside ``[A-Za-z0-9._-]``.
    """
    if not TEAM_ID_PATTERN.fullmatch(team_id):
        raise ValueError(
            f"{team_id!r} is not a usable team id: letters, digits, dot, dash and underscore only"
        )


def _check_token(token: str) -> None:
    """Reject a token a client could not send in a header.

    Parameters
    ----------
    token : str
        The candidate token, stored verbatim.

    Raises
    ------
    ValueError
        When the token is empty or carries whitespace.
    """
    if not token or any(character.isspace() for character in token):
        raise ValueError("a token cannot be empty or contain whitespace")


class TokenPool(RootModel[dict[str, str]]):
    """The set of participant credentials, keyed by team id.

    Tokens are held verbatim rather than hashed: this repository has no hashing capability, and the
    verbatim value is what makes the file reusable across instances and handable to the team it
    belongs to. Every mutation validates what it would write, so an invalid id or token can never
    reach the file.
    """

    @field_validator("root")
    @classmethod
    def _validate_entries(cls, teams: dict[str, str]) -> dict[str, str]:
        """Check every entry, since a hand-edited file is as likely as one this command wrote.

        Parameters
        ----------
        teams : dict of str to str
            The mapping being validated.

        Returns
        -------
        dict of str to str
            The same mapping, once each entry is known to be usable.

        Raises
        ------
        ValueError
            When an id or a token is unusable.
        """
        for team_id, token in teams.items():
            _check_team_id(team_id)
            _check_token(token)
        return teams

    @classmethod
    def load(cls, path: Path) -> TokenPool:
        """Read the pool, treating a file that does not exist as an empty one.

        Parameters
        ----------
        path : Path
            The pool file.

        Returns
        -------
        TokenPool
            The pool on disk, or an empty pool when there is no file yet.

        Raises
        ------
        TokenPoolError
            When the file exists but is not a JSON object of usable entries. The message names the
            path, because the operator has to know which file is wrong.
        """
        if not path.exists():
            return cls({})
        try:
            document: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TokenPoolError(f"{path}: {exc}") from exc
        if not isinstance(document, dict):
            raise TokenPoolError(f"{path}: expected a JSON object of team id to token")
        try:
            return cls(document)
        except ValidationError as exc:
            raise TokenPoolError(f"{path}: {exc}") from exc

    def save(self, path: Path) -> None:
        """Write the pool, private to its owner and never half-written.

        The document is rendered sorted and indented so that two copies of it can be compared with
        ``diff`` — the copy kept off the host is refreshed by hand after every change, and a stable
        rendering is what makes that a one-line check. The parent directory is created 0700 when it
        is missing and left alone when it exists, since an existing directory may not be ours.

        Parameters
        ----------
        path : Path
            Where to write.

        Raises
        ------
        OSError
            When the directory cannot be created or the file cannot be replaced.
        """
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        body = json.dumps(self.root, indent=2, sort_keys=True) + "\n"
        # Written beside the target and moved into place: the gateway reads this file, and a reader
        # must see either the previous pool or this one.
        handle, staged = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(body)
            os.chmod(staged, 0o600)
            os.replace(staged, path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(staged)
            raise

    def team_ids(self) -> list[str]:
        """Return the team ids in a stable order.

        Returns
        -------
        list of str
            The ids, sorted, with no tokens among them.
        """
        return sorted(self.root)

    def issue(self, team_id: str, token: str | None = None, *, replace: bool = False) -> str:
        """Add a team, or rotate its token, and return the token.

        Parameters
        ----------
        team_id : str
            The team to add.
        token : str, optional
            The token to store; a fresh one is generated when this is omitted.
        replace : bool, default False
            Whether an id that already has a token may be given a new one.

        Returns
        -------
        str
            The token now stored for ``team_id``.

        Raises
        ------
        TokenPoolError
            When the id already exists and ``replace`` is false, or when the id or the token is
            unusable.
        """
        if team_id in self.root and not replace:
            raise TokenPoolError(f"{team_id} already has a token — pass --force to replace it")
        value = token if token is not None else secrets.token_urlsafe(TOKEN_BYTES)
        try:
            validated = type(self)({**self.root, team_id: value})
        except ValidationError as exc:
            raise TokenPoolError(str(exc)) from exc
        self.root = validated.root
        return value

    def revoke(self, team_id: str) -> None:
        """Remove a team from the pool.

        Parameters
        ----------
        team_id : str
            The team to remove.

        Raises
        ------
        TokenPoolError
            When the id is not in the pool, so that a typo cannot look like a revocation.
        """
        if team_id not in self.root:
            raise TokenPoolError(f"{team_id} is not in the pool")
        remaining = dict(self.root)
        del remaining[team_id]
        self.root = remaining
