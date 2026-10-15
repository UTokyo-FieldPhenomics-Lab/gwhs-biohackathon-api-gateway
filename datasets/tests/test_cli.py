"""Tests for the two-subcommand command line."""

from __future__ import annotations

from pathlib import Path

import pytest

from biohack_datasets import cli
from biohack_datasets.sources import Source, Upstream

UPSTREAM = Upstream(
    "owner/repo",
    "d223a227ee21fca3dbab9605d3fbcdcc2e33abc5",
    ("data/*.parquet",),
)


def _source(adapter: str = "gwhs") -> Source:
    """Return a pinned dataset the tests can patch into the provenance record."""
    return Source("demo", UPSTREAM, adapter, None, None)


def test_publish_dry_run_prints_the_bucket_and_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Acceptance one: the plan names the bucket and nothing is created."""
    monkeypatch.setattr(cli, "load_sources", lambda: {"demo": _source()})
    monkeypatch.setenv("BIOHACK_BUCKET", "s3://demo-bucket")
    index_dir = tmp_path / "index"

    code = cli.main(
        [
            "publish",
            "--dataset",
            "demo",
            "--dry-run",
            "--index-dir",
            str(index_dir),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert code == 0
    assert "s3://demo-bucket" in capsys.readouterr().out
    assert not index_dir.exists()


def test_an_unimplemented_adapter_exits_one_and_names_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A missing reader is a hard error, never a silent skip."""
    monkeypatch.setattr(cli, "load_sources", lambda: {"demo": _source("gwfss")})

    code = cli.main(
        [
            "publish",
            "--dataset",
            "demo",
            "--index-dir",
            str(tmp_path / "index"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert code == 1
    assert "gwfss" in capsys.readouterr().err


def test_a_missing_cache_exits_one_and_names_fetch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Publish never downloads; the error points at the command that does."""
    monkeypatch.setattr(cli, "load_sources", lambda: {"demo": _source()})
    monkeypatch.delenv("BIOHACK_BUCKET", raising=False)

    code = cli.main(
        [
            "publish",
            "--dataset",
            "demo",
            "--index-dir",
            str(tmp_path / "index"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert code == 1
    assert "biohack-datasets fetch" in capsys.readouterr().err


def test_the_bucket_flag_wins_over_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An explicit flag overrides the environment, as the flag table says."""
    monkeypatch.setattr(cli, "load_sources", lambda: {"demo": _source()})
    monkeypatch.setenv("BIOHACK_BUCKET", "s3://from-env")

    code = cli.main(
        [
            "publish",
            "--dataset",
            "demo",
            "--bucket",
            "s3://from-flag",
            "--dry-run",
            "--index-dir",
            str(tmp_path / "index"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "s3://from-flag" in out
    assert "s3://from-env" not in out


def test_a_publish_session_is_built_from_the_console_csv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """BIOHACK_ACCESS_KEYS_CSV names the credential; its pair is handed to the session."""
    path = tmp_path / "awsAccessKeys.csv"
    path.write_bytes(b"\xef\xbb\xbfAccess key ID,Secret access key\nIDEXAMPLE,SECRET\n")
    monkeypatch.setenv("BIOHACK_ACCESS_KEYS_CSV", str(path))
    seen: dict[str, str] = {}

    def fake_session(keys: object) -> str:
        seen["id"] = keys.access_key_id  # type: ignore[attr-defined]
        return "SESSION"

    monkeypatch.setattr(cli, "session_from_access_keys", fake_session)

    assert cli._publish_session() == "SESSION"
    assert seen == {"id": "IDEXAMPLE"}


def test_without_the_csv_variable_boto3s_own_chain_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """A laptop keeps using its profile when no CSV is named."""
    monkeypatch.delenv("BIOHACK_ACCESS_KEYS_CSV", raising=False)

    assert cli._publish_session() is None


def test_a_malformed_csv_exits_one_without_naming_a_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A bad credential file is an operator error, reported without the secret."""
    path = tmp_path / "awsAccessKeys.csv"
    path.write_bytes(b"Key,Secret\nIDEXAMPLE,VERYSECRET\n")
    monkeypatch.setattr(cli, "load_sources", lambda: {"demo": _source()})
    monkeypatch.setenv("BIOHACK_ACCESS_KEYS_CSV", str(path))

    code = cli.main(
        [
            "publish",
            "--dataset",
            "demo",
            "--bucket",
            "s3://demo",
            "--index-dir",
            str(tmp_path / "index"),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )

    assert code == 1
    err = capsys.readouterr().err
    assert "Access key ID" in err
    assert "VERYSECRET" not in err
