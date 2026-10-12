"""Tests for the cache step: what it asks the Hub for, and what it checks afterwards."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from huggingface_hub.file_download import DryRunFileInfo

from biohack_datasets import fetch_cache
from biohack_datasets.fetch_cache import CacheVerificationError, download, main, plan, verify
from biohack_datasets.sources import Source, Upstream

UPSTREAM = Upstream(
    repo="owner/repo",
    revision="d223a227ee21fca3dbab9605d3fbcdcc2e33abc5",
    include=("data/*.parquet",),
)


def _dry_file(name: str, size: int = 1234) -> DryRunFileInfo:
    """Build the plan entry the Hub would return for ``name``."""
    return DryRunFileInfo(
        commit_hash=UPSTREAM.revision,
        file_size=size,
        filename=name,
        local_path=f"/cache/{name}",
        is_cached=False,
        will_download=True,
    )


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for the Hub client, recording every call it receives."""
    calls: list[dict[str, Any]] = []

    def fake_snapshot_download(repo_id: str, **kwargs: Any) -> Any:
        calls.append({"repo_id": repo_id, **kwargs})
        if kwargs.get("dry_run"):
            return [_dry_file("data/part-00000-of-00001.parquet")]
        return ["/cache/data/part-00000-of-00001.parquet"]

    monkeypatch.setattr(fetch_cache, "snapshot_download", fake_snapshot_download)
    return calls


def test_plan_asks_for_the_pinned_revision_and_the_selected_files(
    hub: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The plan is a read-only request carrying repository, revision, and selection."""
    files = plan(UPSTREAM, tmp_path)

    assert [file.filename for file in files] == ["data/part-00000-of-00001.parquet"]
    assert hub == [
        {
            "repo_id": "owner/repo",
            "repo_type": "dataset",
            "revision": UPSTREAM.revision,
            "allow_patterns": ["data/*.parquet"],
            "local_dir": tmp_path,
            "dry_run": True,
        }
    ]


def test_download_uses_the_pinned_revision(hub: list[dict[str, Any]], tmp_path: Path) -> None:
    """The real download carries the same revision and selection as the plan."""
    download(UPSTREAM, tmp_path, concurrency=3)

    assert hub[0]["repo_id"] == "owner/repo"
    assert hub[0]["revision"] == UPSTREAM.revision
    assert hub[0]["allow_patterns"] == ["data/*.parquet"]
    assert hub[0]["max_workers"] == 3
    assert "dry_run" not in hub[0]


def test_dry_run_reports_the_plan_and_fetches_nothing(
    hub: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """`--dry-run` prints what would be fetched and issues no download."""
    monkeypatch.setattr(fetch_cache, "load_sources", lambda: {"gwhd": Source("gwhd", UPSTREAM)})

    assert main(["--dry-run", "--cache-dir", str(tmp_path)]) == 0

    assert "1 to download" in capsys.readouterr().out
    assert [call.get("dry_run") for call in hub] == [True]


def test_a_dataset_without_a_pinned_revision_is_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`gwhs` is in the record but not in the run."""
    monkeypatch.setattr(fetch_cache, "load_sources", lambda: {"gwhs": Source("gwhs", None)})

    assert main(["--dataset", "gwhs"]) == 0

    assert "skipped" in capsys.readouterr().out


def test_verify_reports_rows_and_keeps_readable_files(tmp_path: Path) -> None:
    """A parquet file yields its row count; a non-parquet file is not a verification target."""
    table = tmp_path / "table.parquet"
    pq.write_table(pa.table({"a": [1, 2, 3]}), table)
    card = tmp_path / "README.md"
    card.write_text("card", encoding="utf-8")

    assert verify([table, card]) == {table: 3}
    assert table.exists() and card.exists()


def test_verify_deletes_a_file_that_is_not_readable_parquet(tmp_path: Path) -> None:
    """A truncated download is removed, so the next run refetches instead of trusting it."""
    truncated = tmp_path / "truncated.parquet"
    truncated.write_bytes(b"PAR1not a parquet file")

    with pytest.raises(CacheVerificationError, match="truncated.parquet"):
        verify([truncated])

    assert not truncated.exists()
