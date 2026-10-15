"""Tests for the publish path: build, upload, skip, repair, conflict, verify."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from biohack_datasets.formats import CACHE_CONTROL, CONTENT_TYPES
from biohack_datasets.index import read_back
from biohack_datasets.publish import (
    MissingCache,
    ObjectConflict,
    VerificationError,
    parse_bucket,
    publish_dataset,
)
from biohack_datasets.s3 import MemoryObjectStore
from biohack_datasets.sources import Source, Upstream

REVISION = "655118534e9ad980b6f7c0dd6566a406d5a29a27"
JPEG = b"\xff\xd8\xff\xe0payload"
PNG = b"\x89PNG\r\n\x1a\npayload"
SOURCE = Source(
    "demo",
    Upstream("owner/repo", REVISION, ("data/*.parquet",)),
    "gwhs",
    "cc-by-4.0",
    None,
)


def _features() -> dict[str, object]:
    """Return the ClassLabel metadata of the synthetic upstream."""
    return {
        "image": {"_type": "Image"},
        "image_id": {"dtype": "string", "_type": "Value"},
        "width": {"dtype": "int32", "_type": "Value"},
        "height": {"dtype": "int32", "_type": "Value"},
        "tags": {"feature": {"names": ["Labo"], "_type": "ClassLabel"}, "_type": "List"},
        "objects": {
            "feature": {
                "category": {"names": ["side1"], "_type": "ClassLabel"},
                "bbox": {
                    "feature": {"dtype": "float32", "_type": "Value"},
                    "length": 4,
                    "_type": "List",
                },
                "rle": {
                    "size": {
                        "feature": {"dtype": "int32", "_type": "Value"},
                        "length": 2,
                        "_type": "List",
                    },
                    "counts": {"dtype": "string", "_type": "Value"},
                },
                "polygons": {
                    "feature": {"feature": {"dtype": "float32", "_type": "Value"}, "_type": "List"},
                    "_type": "List",
                },
                "area": {"dtype": "float32", "_type": "Value"},
                "attributes": {
                    "feature": {"names": ["Green"], "_type": "ClassLabel"},
                    "_type": "List",
                },
                "attributes_onehot": {
                    "feature": {"dtype": "int8", "_type": "Value"},
                    "length": 5,
                    "_type": "List",
                },
            },
            "_type": "List",
        },
    }


def _schema() -> pa.Schema:
    """Return the synthetic upstream's Arrow schema."""
    rle = pa.struct([("size", pa.list_(pa.int32(), 2)), ("counts", pa.string())])
    obj = pa.struct(
        [
            ("category", pa.int64()),
            ("bbox", pa.list_(pa.float32(), 4)),
            ("rle", rle),
            ("polygons", pa.list_(pa.list_(pa.float32()))),
            ("area", pa.float32()),
            ("attributes", pa.list_(pa.int64())),
            ("attributes_onehot", pa.list_(pa.int8(), 5)),
        ]
    )
    return pa.schema(
        [
            ("image", pa.struct([("bytes", pa.binary()), ("path", pa.string())])),
            ("image_id", pa.string()),
            ("width", pa.int32()),
            ("height", pa.int32()),
            ("tags", pa.list_(pa.int64())),
            ("tags_onehot", pa.list_(pa.int8(), 18)),
            ("objects", pa.list_(obj)),
        ]
    ).with_metadata({"huggingface": json.dumps({"info": {"features": _features()}})})


def _record(body: bytes, name: str) -> dict[str, object]:
    """Return one synthetic parquet record with a single object."""
    return {
        "image": {"bytes": body, "path": name},
        "image_id": name,
        "width": 4,
        "height": 3,
        "tags": [0],
        "tags_onehot": [0] * 18,
        "objects": [
            {
                "category": 0,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "rle": {"size": [3, 4], "counts": "Z0"},
                "polygons": [],
                "area": 12.0,
                "attributes": [0],
                "attributes_onehot": [0] * 5,
            }
        ],
    }


def _cache(tmp_path: Path) -> Path:
    """Write a two-row synthetic cache and return the cache root."""
    directory = tmp_path / "cache" / "demo" / "data"
    directory.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([_record(JPEG, "a.JPG"), _record(PNG, "b.png")], schema=_schema())
    pq.write_table(table, directory / "train-00000-of-00001.parquet")
    return tmp_path / "cache"


def _publish(tmp_path: Path, store: MemoryObjectStore | None, **overrides: object) -> object:
    """Run one publish against a fresh cache and return its summary."""
    arguments: dict[str, object] = {
        "cache_dir": _cache(tmp_path),
        "index_path": tmp_path / "index" / "demo.db",
        "bucket": "demo-bucket" if store is not None else None,
        "store": store,
        "concurrency": 2,
        "dry_run": False,
        "emit": lambda _line: None,
    }
    arguments.update(overrides)
    return publish_dataset(SOURCE, **arguments)  # type: ignore[arg-type]


def test_a_publish_builds_the_index_and_uploads_every_object(tmp_path: Path) -> None:
    """Image objects go to S3 with their type and cache lifetime; the index lands too."""
    store = MemoryObjectStore()

    summary = _publish(tmp_path, store)

    assert summary.rows == 2  # type: ignore[attr-defined]
    assert summary.objects == 2  # type: ignore[attr-defined]
    assert summary.uploaded == 2  # type: ignore[attr-defined]
    assert set(store.objects) == {"demo/images/00000000.jpg", "demo/images/00000001.png"}
    for key, (_body, content_type, cache_control) in store.objects.items():
        extension = key.rsplit(".", 1)[1]
        assert content_type == CONTENT_TYPES[extension]
        assert cache_control == CACHE_CONTROL
    tables = read_back(tmp_path / "index" / "demo.db")
    assert len(tables["rows"]) == 2
    assert len(tables["objects"]) == 2


def test_a_second_publish_skips_every_object(tmp_path: Path) -> None:
    """Idempotence by comparison: the same bytes and headers upload nothing."""
    store = MemoryObjectStore()
    _publish(tmp_path, store)

    summary = _publish(tmp_path, store)

    assert summary.uploaded == 0  # type: ignore[attr-defined]
    assert summary.skipped == 2  # type: ignore[attr-defined]


def test_an_object_with_a_wrong_header_is_repaired_by_re_putting_the_same_bytes(
    tmp_path: Path,
) -> None:
    """Equal bytes with a different header are not a content change."""
    store = MemoryObjectStore()
    _publish(tmp_path, store)
    key = "demo/images/00000000.jpg"
    body, _content_type, cache_control = store.objects[key]
    store.objects[key] = (body, "application/octet-stream", cache_control)

    summary = _publish(tmp_path, store)

    assert summary.repaired == 1  # type: ignore[attr-defined]
    assert summary.skipped == 1  # type: ignore[attr-defined]
    assert store.objects[key][1] == CONTENT_TYPES["jpg"]


def test_an_object_with_different_bytes_stops_and_names_the_key(tmp_path: Path) -> None:
    """A key that already holds other bytes is a new dataset id or a deliberate wipe."""
    store = MemoryObjectStore()
    _publish(tmp_path, store)
    store.tamper("demo/images/00000000.jpg", JPEG + b"more")

    with pytest.raises(ObjectConflict, match="demo/images/00000000.jpg"):
        _publish(tmp_path, store)


def test_an_upload_that_does_not_read_back_is_a_verification_error(tmp_path: Path) -> None:
    """The bucket's answer after the write is what authorises the object."""

    class _LyingStore(MemoryObjectStore):
        def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
            etag = super().put(key, body, content_type=content_type, cache_control=cache_control)
            _, stored_type, stored_control = self.objects[key]
            self.objects[key] = (b"X" * len(body), stored_type, stored_control)
            return etag

    with pytest.raises(VerificationError, match="demo/images/00000000.jpg"):
        _publish(tmp_path, _LyingStore())


def test_a_missing_cache_names_the_fetch_command(tmp_path: Path) -> None:
    """A publish never downloads; it says what would."""
    with pytest.raises(MissingCache, match="biohack-datasets fetch"):
        publish_dataset(
            SOURCE,
            cache_dir=tmp_path / "empty",
            index_path=tmp_path / "index" / "demo.db",
            bucket=None,
            store=None,
            concurrency=1,
            dry_run=False,
            emit=lambda _line: None,
        )


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    """The plan is printed and no index, object, or directory appears."""
    lines: list[str] = []
    index_path = tmp_path / "index" / "demo.db"

    summary = _publish(tmp_path, None, index_path=index_path, dry_run=True, emit=lines.append)

    assert summary is None
    assert not index_path.exists()
    assert any("bucket none" in line for line in lines)


def test_github_style_bucket_references_parse_to_a_name() -> None:
    """The bucket setting accepts with and without the scheme."""
    assert parse_bucket("s3://demo") == "demo"
    assert parse_bucket("demo") == "demo"
    assert parse_bucket(None) is None
