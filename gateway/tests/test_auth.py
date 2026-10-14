"""The pool's read half: what the gateway accepts as a key.

The write half is the ``biohack-gateway tokens`` command; this is the side that answers a request.
The two share :mod:`biohack_gateway.tokens` and therefore the file format, which is the reason the
command lives in this package rather than beside it.

The pool is re-read whenever the file changes, so a token issued, rotated or revoked during an event
takes effect on the next request. Revocation is the reason that matters: a leaked key must stop
working without a human remembering a command.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from biohack_gateway.auth import Authenticator
from biohack_gateway.tokens import TokenPool, TokenPoolError

TEAM = "gw-01"
TOKEN = "zR3fTqk8Wm2Nv5Yc7Bd9Lh1Jp4Xs6Ag0Eu"
SECOND_TEAM = "gw-02"
SECOND_TOKEN = "Qm7Hd2Kn9Pv4Wx1Zc5Bt8Lr3Js6Fy0Ug"


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


def test_a_change_is_picked_up_without_a_restart(tmp_path: Path) -> None:
    """Issuing and revoking both take effect on the next request, with nothing to remember."""
    path = _pool(tmp_path / "tokens.json", {TEAM: TOKEN})
    authenticator = Authenticator(path)
    assert authenticator.team_for(TOKEN) == TEAM
    assert authenticator.team_for(SECOND_TOKEN) is None

    _pool(path, {TEAM: TOKEN, SECOND_TEAM: SECOND_TOKEN})
    assert authenticator.team_for(SECOND_TOKEN) == SECOND_TEAM

    _pool(path, {TEAM: TOKEN})
    assert authenticator.team_for(SECOND_TOKEN) is None, "a revoked token must stop working at once"


def test_a_pool_that_appears_later_is_read(tmp_path: Path) -> None:
    """The common first run: the gateway is serving before anybody has been issued a token."""
    path = tmp_path / "tokens.json"
    authenticator = Authenticator(path)
    assert authenticator.team_for(TOKEN) is None

    _pool(path, {TEAM: TOKEN})
    assert authenticator.team_for(TOKEN) == TEAM


def test_a_broken_pool_never_displaces_a_good_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo mid-event must not lock out participants: it is reported, the last pool stands."""
    path = _pool(tmp_path / "tokens.json", {TEAM: TOKEN})
    authenticator = Authenticator(path)
    assert authenticator.team_for(TOKEN) == TEAM

    with caplog.at_level(logging.ERROR):
        path.write_text("{]. broken", encoding="utf-8")
        assert authenticator.team_for(TOKEN) == TEAM, "the last pool that parsed is still answering"
        assert authenticator.team_for(TOKEN) == TEAM, "and re-reading is not attempted per request"

    assert len(caplog.records) == 1, "one line per change, not one per request"
    assert "token pool unusable" in caplog.text
    assert "tokens.json" in caplog.text, "the operator needs to know which file is wrong"

    _pool(path, {TEAM: TOKEN, SECOND_TEAM: SECOND_TOKEN})
    assert authenticator.team_for(SECOND_TOKEN) == SECOND_TEAM, "fixing the file is enough"


def test_a_pool_that_never_parsed_keeps_saying_so(tmp_path: Path) -> None:
    """With no good pool to fall back on the error stands, and the first good one ends it."""
    path = tmp_path / "tokens.json"
    path.write_text("{]. broken", encoding="utf-8")
    authenticator = Authenticator(path)

    with pytest.raises(TokenPoolError):
        authenticator.team_for(TOKEN)
    with pytest.raises(TokenPoolError):
        authenticator.team_for(TOKEN)

    _pool(path, {TEAM: TOKEN})
    assert authenticator.team_for(TOKEN) == TEAM


def test_a_shared_token_is_reported(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A duplicated token is an operator mistake worth a log line, not an outage for everyone."""
    shared = "one-token-for-two-teams"
    path = _pool(tmp_path / "tokens.json", {TEAM: shared, SECOND_TEAM: shared})

    with caplog.at_level(logging.WARNING):
        authenticator = Authenticator(path)

    assert authenticator.team_for(shared) == TEAM
    assert SECOND_TEAM in caplog.text
