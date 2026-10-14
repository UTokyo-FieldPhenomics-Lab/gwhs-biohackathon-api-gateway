"""The pool's read half: what the gateway accepts as a key.

The write half is the ``biohack-gateway tokens`` command; this is the side that answers a request.
The two share :mod:`biohack_gateway.tokens` and therefore the file format, which is the reason the
command lives in this package rather than beside it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from biohack_gateway.auth import Authenticator
from biohack_gateway.tokens import TokenPool, TokenPoolError

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"


def _pool(path: Path, teams: dict[str, str]) -> Path:
    """Write a pool to ``path`` and return it, as the token command would.

    Parameters
    ----------
    path : Path
        Where to write.
    teams : dict of str to str
        The team ids and tokens to store.

    Returns
    -------
    Path
        The path written, so a test can pass it straight to the authenticator.
    """
    TokenPool(teams).save(path)
    return path


def test_a_valid_token_names_its_team(tmp_path: Path) -> None:
    """A token the pool holds is answered with its team id, which is what the logs attribute to."""
    authenticator = Authenticator(_pool(tmp_path / "tokens.json", {TEAM: TOKEN}))
    assert authenticator.team_for(TOKEN) == TEAM


def test_an_unknown_token_names_nobody(tmp_path: Path) -> None:
    """A token nobody issued is refused rather than guessed at."""
    authenticator = Authenticator(_pool(tmp_path / "tokens.json", {TEAM: TOKEN}))
    assert authenticator.team_for("not-a-token-this-project-issued") is None


def test_a_missing_pool_authenticates_nobody(tmp_path: Path) -> None:
    """No file yet is a legal state — the operator has issued nothing — and not an error."""
    authenticator = Authenticator(tmp_path / "nothing-here.json")
    assert authenticator.team_for(TOKEN) is None


def test_an_unreadable_pool_is_an_error(tmp_path: Path) -> None:
    """A pool that does not parse is the operator's problem, and is raised rather than swallowed."""
    broken = tmp_path / "tokens.json"
    broken.write_text("{]. not json", encoding="utf-8")

    with pytest.raises(TokenPoolError):
        Authenticator(broken).team_for(TOKEN)


def test_an_unreadable_pool_is_reread_rather_than_remembered(tmp_path: Path) -> None:
    """Fixing the file is enough: a failure is not cached, so the running gateway heals."""
    broken = tmp_path / "tokens.json"
    broken.write_text("{]. not json", encoding="utf-8")
    authenticator = Authenticator(broken)

    with pytest.raises(TokenPoolError):
        authenticator.team_for(TOKEN)

    _pool(broken, {TEAM: TOKEN})
    assert authenticator.team_for(TOKEN) == TEAM


def test_the_pool_is_read_once(tmp_path: Path) -> None:
    """A change applies on restart, like every other read-into-memory surface."""
    path = _pool(tmp_path / "tokens.json", {TEAM: TOKEN})
    authenticator = Authenticator(path)
    assert authenticator.team_for(TOKEN) == TEAM

    _pool(path, {TEAM: TOKEN, "gw-02": "an-added-token"})
    assert authenticator.team_for("an-added-token") is None


def test_a_shared_token_is_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A duplicated token is an operator mistake worth a log line, not an outage for everyone."""
    shared = "one-token-for-two-teams"
    path = _pool(tmp_path / "tokens.json", {TEAM: shared, "gw-02": shared})

    with caplog.at_level(logging.WARNING):
        authenticator = Authenticator(path)

    assert authenticator.team_for(shared) == TEAM
    assert "gw-02" in caplog.text
