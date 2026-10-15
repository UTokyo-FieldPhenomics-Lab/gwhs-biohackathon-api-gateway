"""Tests against the real upstream bytes: the gwhs adapter over the pinned sample.

Deselected by default (`-m 'not network'`). Run them where the Hub is reachable:
``uv run pytest -m network``. The sample is 11.4 MB, which is the smallest real dataset there
is, so this is the test that proves the adapter against bytes rather than a fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from biohack_datasets.adapters.gwhs import GwhsAdapter
from biohack_datasets.fetch_cache import download
from biohack_datasets.publish import parquet_files
from biohack_datasets.sources import Source, Upstream, load_sources

pytestmark = pytest.mark.network


def test_the_gwhs_adapter_reads_the_pinned_sample(tmp_path: Path) -> None:
    """Ten rows, 199 objects, both encodings, and every object's RLE present."""
    source: Source = load_sources()["gwhs-temp"]
    upstream: Upstream = source.upstream  # type: ignore[assignment]
    download(upstream, tmp_path / source.dataset_id, concurrency=1)

    rows = list(GwhsAdapter().iter_rows(parquet_files(tmp_path, source.dataset_id, upstream)))

    assert [row.row for row in rows] == list(range(10))
    assert sum(len(row.objects) for row in rows) == 199
    assert {row.image_ext for row in rows} == {"jpg", "png"}
    assert all(row.objects for row in rows)
    assert sum(annotation.rle is not None for row in rows for annotation in row.objects) == 199
