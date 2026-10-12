"""Tests against the real Hugging Face Hub: the pinned plan, and a download that stays small.

Deselected by default (`-m 'not network'`). Run them where the Hub is reachable, on the host
that will hold the cache: ``uv run pytest -m network``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from biohack_datasets.fetch_cache import download, plan, verify
from biohack_datasets.sources import Source, Upstream, load_sources

pytestmark = pytest.mark.network

PINNED_FILES = {
    "gwhd": {
        "data/train-00000-of-00002.parquet",
        "data/train-00001-of-00002.parquet",
        "data/validation-00000-of-00001.parquet",
        "data/test-00000-of-00001.parquet",
    },
    "gwfss": {
        "data/labelled-00000-of-00002.parquet",
        "data/labelled-00001-of-00002.parquet",
    },
    "gwhs-temp": {"data/train-00000-of-00001.parquet"},
}


def _upstream(source: Source) -> Upstream:
    """Return the source's upstream, failing if it has none."""
    assert source.upstream is not None
    return source.upstream


@pytest.mark.parametrize("dataset_id", sorted(PINNED_FILES))
def test_the_pinned_revision_offers_exactly_the_selected_files(
    dataset_id: str, tmp_path: Path
) -> None:
    """Names, sizes, and nothing else: no upstream archive or card is ever selected."""
    files = plan(_upstream(load_sources()[dataset_id]), tmp_path / dataset_id)

    assert {file.filename for file in files} == PINNED_FILES[dataset_id]
    assert all(file.file_size for file in files)


def test_only_the_selected_file_is_fetched(tmp_path: Path) -> None:
    """The selection is the whole filter: name the card and the card is all that lands."""
    upstream = dataclasses.replace(_upstream(load_sources()["gwhs-temp"]), include=("README.md",))
    (declared,) = plan(upstream, tmp_path)

    download(upstream, tmp_path, concurrency=1)

    assert not list(tmp_path.rglob("*.parquet"))
    assert (tmp_path / "README.md").stat().st_size == declared.file_size
    assert verify([Path(declared.local_path)]) == {}
