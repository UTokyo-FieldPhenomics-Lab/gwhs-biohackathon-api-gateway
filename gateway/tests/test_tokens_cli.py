"""Tests for the ``biohack-gateway tokens`` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import CaptureFixture

from biohack_gateway.cli import main


def test_add_prints_the_token_and_writes_the_pool(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Only the token reaches stdout; the file path and what to do next go to stderr."""
    path = tmp_path / "tokens.json"
    assert main(["tokens", "add", "--id", "gw-01", "--file", str(path)]) == 0

    out, err = capsys.readouterr()
    token = out.strip()
    assert token and "\n" not in token
    assert json.loads(path.read_text(encoding="utf-8")) == {"gw-01": token}
    assert "written" in err
    assert "next request" in err


def test_list_prints_team_ids_and_never_tokens(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Output safe to paste anywhere: the ids are the report, the tokens are the credentials."""
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"gw-02": "secret-b", "gw-01": "secret-a"}), encoding="utf-8")

    assert main(["tokens", "list", "--file", str(path)]) == 0

    out, err = capsys.readouterr()
    assert out == "gw-01\ngw-02\n"
    assert "secret" not in out
    assert err == ""


def test_an_empty_pool_lists_nothing(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """No pool file is not an error: it is the state before the first team is issued one."""
    assert main(["tokens", "list", "--file", str(tmp_path / "tokens.json")]) == 0
    assert capsys.readouterr().out == ""


def test_add_refuses_a_team_that_already_has_one(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Silently replacing a token would break a working team, so it takes an explicit flag."""
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"gw-01": "in-use"}), encoding="utf-8")

    assert main(["tokens", "add", "--id", "gw-01", "--file", str(path)]) == 1

    out, err = capsys.readouterr()
    assert out == ""
    assert "gw-01" in err
    assert json.loads(path.read_text(encoding="utf-8")) == {"gw-01": "in-use"}


def test_force_replaces_the_token_and_keeps_the_team(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Rotating a leaked token must not cost the team its id in the logs."""
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"gw-01": "leaked"}), encoding="utf-8")

    assert main(["tokens", "add", "--id", "gw-01", "--force", "--file", str(path)]) == 0

    replacement = capsys.readouterr().out.strip()
    assert replacement and replacement != "leaked"
    assert json.loads(path.read_text(encoding="utf-8")) == {"gw-01": replacement}


def test_an_explicit_token_can_be_installed(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """A token that already exists elsewhere can be adopted verbatim."""
    path = tmp_path / "tokens.json"
    assert main(["tokens", "add", "--id", "gw-01", "--token", "given", "--file", str(path)]) == 0
    assert capsys.readouterr().out.strip() == "given"
    assert json.loads(path.read_text(encoding="utf-8")) == {"gw-01": "given"}


def test_remove_deletes_the_team(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """Revocation is one command, and it reports where it wrote so the copy can follow."""
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"gw-01": "a", "gw-02": "b"}), encoding="utf-8")

    assert main(["tokens", "remove", "--id", "gw-01", "--file", str(path)]) == 0

    out, err = capsys.readouterr()
    assert out == ""
    assert "written" in err
    assert json.loads(path.read_text(encoding="utf-8")) == {"gw-02": "b"}


def test_remove_of_an_unknown_team_fails(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    """A typo must not look like a successful revocation."""
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps({"gw-01": "a"}), encoding="utf-8")

    assert main(["tokens", "remove", "--id", "gw-99", "--file", str(path)]) == 1

    assert "gw-99" in capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8")) == {"gw-01": "a"}


def test_a_malformed_pool_file_fails_without_being_rewritten(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """The command is not a repair tool: it reports the file and leaves it alone."""
    path = tmp_path / "tokens.json"
    path.write_text("{not json", encoding="utf-8")

    assert main(["tokens", "add", "--id", "gw-01", "--file", str(path)]) == 1

    assert "tokens.json" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == "{not json"


def test_the_default_file_is_the_host_state_directory(
    capsys: CaptureFixture[str], tmp_path: Path
) -> None:
    """The path the service reads is the default, so the host needs no configuration for it."""
    from biohack_gateway.tokens import DEFAULT_TOKENS_FILE

    assert DEFAULT_TOKENS_FILE.is_relative_to(Path.home())
    assert DEFAULT_TOKENS_FILE.parts[-3:] == (".config", "biohack-gateway", "tokens.json")


@pytest.mark.parametrize("argv", [["tokens"], ["tokens", "add"], []])
def test_a_missing_verb_or_id_is_a_usage_error(argv: list[str]) -> None:
    """A usage error comes from argparse, so its exit status differs from a domain failure."""
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2
