"""Tests for reading the provenance record."""

from __future__ import annotations

from pathlib import Path

import pytest

from biohack_datasets.sources import DEFAULT_INCLUDE, Source, Upstream, load_sources

RECORD = """
datasets:
  gwhd:
    adapter: gwhd
    upstream: Etienne-David/GlobalWheatHeadDataset2021@d223a227ee21fca3dbab9605d3fbcdcc2e33abc5
  gwfss:
    adapter: gwfss
    upstream: GlobalWheat/GWFSS_v1.0@d32eb4a70811fa4107000e0b8821e892df710830
    include: ["data/labelled-*.parquet"]
  gwhs:
    adapter: gwhs
    upstream: null
"""


def _write(tmp_path: Path, text: str) -> Path:
    """Write a provenance record into ``tmp_path`` and return its path."""
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _upstream(source: Source) -> Upstream:
    """Return the source's upstream, failing if it has none."""
    assert source.upstream is not None
    return source.upstream


def test_a_dataset_without_include_fetches_the_parquet_under_data(tmp_path: Path) -> None:
    """The default selection is the whole rule: parquet under `data/`, nothing else."""
    sources = load_sources(_write(tmp_path, RECORD))
    assert _upstream(sources["gwhd"]).include == DEFAULT_INCLUDE


def test_include_overrides_the_default_selection(tmp_path: Path) -> None:
    """A dataset may narrow the selection to one split of its upstream."""
    sources = load_sources(_write(tmp_path, RECORD))
    assert _upstream(sources["gwfss"]).include == ("data/labelled-*.parquet",)


def test_an_unpinned_dataset_has_no_upstream(tmp_path: Path) -> None:
    """`upstream: null` parses into a dataset that every consumer skips."""
    sources = load_sources(_write(tmp_path, RECORD))
    assert sources["gwhs"].upstream is None


@pytest.mark.parametrize(
    "upstream",
    [
        "Etienne-David/GlobalWheatHeadDataset2021",
        "Etienne-David/GlobalWheatHeadDataset2021@main",
        "@d223a227ee21fca3dbab9605d3fbcdcc2e33abc5",
        "Etienne-David/GlobalWheatHeadDataset2021@d223a227",
    ],
)
def test_a_repository_without_a_commit_sha_is_rejected(tmp_path: Path, upstream: str) -> None:
    """A moving or malformed revision would let a published address drift."""
    record = f"datasets:\n  gwhd:\n    adapter: gwhd\n    upstream: '{upstream}'\n"
    with pytest.raises(ValueError, match="commit sha"):
        load_sources(_write(tmp_path, record))


def test_the_shipped_record_pins_commits_and_stays_under_data() -> None:
    """The real file: every upstream pinned, no pattern reaching an upstream archive."""
    sources = load_sources()
    pinned = {
        dataset_id: _upstream(source)
        for dataset_id, source in sources.items()
        if source.upstream is not None
    }
    assert sorted(pinned) == ["gwfss", "gwhd", "gwhs-temp"]
    for upstream in pinned.values():
        assert upstream.include
        assert all(pattern.startswith("data/") for pattern in upstream.include)
