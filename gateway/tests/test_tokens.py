"""Tests for the participant token pool: the file the CLI writes and the gateway reads."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from biohack_gateway.tokens import TokenPool, TokenPoolError


def test_a_missing_file_loads_as_an_empty_pool(tmp_path: Path) -> None:
    """The first `add` must not need the file to exist."""
    assert TokenPool.load(tmp_path / "tokens.json").team_ids() == []


def test_the_pool_is_written_private_under_a_private_directory(tmp_path: Path) -> None:
    """A pool is a credential for every team, so nothing about it is group- or world-readable."""
    path = tmp_path / "nested" / "tokens.json"
    pool = TokenPool({})
    pool.issue("gw-01")
    pool.save(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_the_file_is_sorted_and_indented_so_copies_can_be_compared(tmp_path: Path) -> None:
    """The off-host copy is refreshed by hand, and a stable rendering is what makes that a diff."""
    path = tmp_path / "tokens.json"
    pool = TokenPool({})
    pool.issue("gw-02")
    pool.issue("gw-01")
    pool.save(path)
    assert (
        path.read_text(encoding="utf-8")
        == json.dumps(
            {"gw-01": pool.root["gw-01"], "gw-02": pool.root["gw-02"]}, indent=2, sort_keys=True
        )
        + "\n"
    )


def test_saving_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    """The pool is replaced, not appended to, so the directory keeps exactly one pool file."""
    path = tmp_path / "tokens.json"
    pool = TokenPool({})
    pool.issue("gw-01")
    pool.save(path)
    pool.issue("gw-02")
    pool.save(path)
    assert [entry.name for entry in tmp_path.iterdir()] == ["tokens.json"]


def test_a_generated_token_is_usable_in_a_header() -> None:
    """``token_urlsafe`` output: long enough to be unguessable, free of whitespace and quoting."""
    token = TokenPool({}).issue("gw-01")
    assert len(token) >= 32
    assert token == token.strip()
    assert not any(character.isspace() for character in token)


def test_two_generated_tokens_differ() -> None:
    """A pool is a set of credentials, not one credential copied around."""
    pool = TokenPool({})
    assert pool.issue("gw-01") != pool.issue("gw-02")


def test_issuing_an_existing_team_is_refused() -> None:
    """Replacing silently would break a team mid-event with no signal to anyone."""
    pool = TokenPool({"gw-01": "first"})
    with pytest.raises(TokenPoolError, match="gw-01"):
        pool.issue("gw-01")
    assert pool.root == {"gw-01": "first"}


def test_issuing_with_replace_keeps_the_id_and_changes_the_token() -> None:
    """Rotation after a leak must not cost the team its identity in the logs."""
    pool = TokenPool({"gw-01": "leaked"})
    assert pool.issue("gw-01", replace=True) != "leaked"
    assert list(pool.root) == ["gw-01"]


def test_an_explicit_token_is_stored_verbatim() -> None:
    """No hash, no transform: the file is the artifact handed to the team."""
    pool = TokenPool({})
    assert pool.issue("gw-01", "hand-written-token") == "hand-written-token"
    assert pool.root["gw-01"] == "hand-written-token"


def test_revoking_a_team_removes_it() -> None:
    """Revocation is deletion from the pool, and the gateway applies it on its next request."""
    pool = TokenPool({"gw-01": "a", "gw-02": "b"})
    pool.revoke("gw-01")
    assert pool.root == {"gw-02": "b"}


def test_revoking_an_unknown_team_is_refused() -> None:
    """A typo must fail loudly rather than write an unchanged file."""
    pool = TokenPool({"gw-01": "a"})
    with pytest.raises(TokenPoolError, match="gw-99"):
        pool.revoke("gw-99")


@pytest.mark.parametrize("team_id", ["", "gw 01", "gw/01", "-gw01", "gw:01", "gw\n01"])
def test_a_team_id_outside_the_charset_is_refused(team_id: str) -> None:
    """Ids reach log lines, URLs and shell commands, so the set is narrow by construction."""
    with pytest.raises(TokenPoolError):
        TokenPool({}).issue(team_id)


@pytest.mark.parametrize("token", ["", "  ", "two words", "line\nbreak"])
def test_a_token_that_cannot_travel_in_a_header_is_refused(token: str) -> None:
    """A token the client cannot send is a token nobody can use."""
    with pytest.raises(TokenPoolError):
        TokenPool({}).issue("gw-01", token)


def test_a_malformed_file_is_reported_with_its_path(tmp_path: Path) -> None:
    """The operator has to be told which file is wrong, not just that something is."""
    path = tmp_path / "tokens.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(TokenPoolError, match="tokens.json"):
        TokenPool.load(path)


def test_a_json_document_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    """A list of tokens would load as nothing and reject every team."""
    path = tmp_path / "tokens.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(TokenPoolError, match="tokens.json"):
        TokenPool.load(path)


def test_a_round_trip_preserves_the_pool(tmp_path: Path) -> None:
    """Reading back what was written is the whole contract between the command and the gateway."""
    path = tmp_path / "tokens.json"
    pool = TokenPool({})
    pool.issue("gw-01")
    pool.issue("gw-02", "given")
    pool.save(path)
    assert TokenPool.load(path).root == pool.root
