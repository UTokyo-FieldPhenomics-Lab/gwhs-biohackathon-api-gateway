"""Tests for the publish path: build, upload, skip, conflict, verify."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import read_back

from biohack_datasets.formats import CACHE_CONTROL, CONTENT_TYPES
from biohack_datasets.publish import (
    IncompleteCache,
    MissingCache,
    ObjectConflict,
    VerificationError,
    missing_shards,
    parse_bucket,
    publish_dataset,
)
from biohack_datasets.s3 import (
    AlreadyExists,
    MemoryObjectStore,
    ObjectInfo,
    ObjectUnreadable,
    StoredObject,
)
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
    for key, stored in store.objects.items():
        extension = key.rsplit(".", 1)[1]
        assert stored.content_type == CONTENT_TYPES[extension]
        assert stored.cache_control == CACHE_CONTROL
    tables = read_back(tmp_path / "index" / "demo.db")
    assert len(tables["rows"]) == 2
    assert len(tables["objects"]) == 2


def test_a_second_publish_skips_every_object(tmp_path: Path) -> None:
    """Idempotence by comparison: matching size and headers upload nothing."""
    store = MemoryObjectStore()
    _publish(tmp_path, store)
    written = {key: stored.etag for key, stored in store.objects.items()}

    summary = _publish(tmp_path, store)

    assert summary.uploaded == 0  # type: ignore[attr-defined]
    assert summary.skipped == 2  # type: ignore[attr-defined]
    assert {key: stored.etag for key, stored in store.objects.items()} == written


def test_a_create_is_attempted_before_the_key_is_inspected(tmp_path: Path) -> None:
    """The bucket is asked to create first; no HEAD decides existence in advance."""

    class _RecordingStore(MemoryObjectStore):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def head(self, key: str) -> ObjectInfo | None:
            self.calls.append(f"head {key}")
            return super().head(key)

        def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
            self.calls.append(f"put {key}")
            return super().put(key, body, content_type=content_type, cache_control=cache_control)

    store = _RecordingStore()

    _publish(tmp_path, store)

    assert store.calls[:2] == [
        "put demo/images/00000000.jpg",
        "head demo/images/00000000.jpg",
    ]


def test_an_object_with_a_wrong_header_is_a_conflict_and_stays_untouched(tmp_path: Path) -> None:
    """A header is not repaired by re-putting: an occupied key is compared and refused."""
    store = MemoryObjectStore()
    _publish(tmp_path, store)
    key = "demo/images/00000000.jpg"
    stored = store.objects[key]
    store.objects[key] = replace(stored, content_type="application/octet-stream")

    with pytest.raises(ObjectConflict, match="demo/images/00000000.jpg"):
        _publish(tmp_path, store)

    assert store.objects[key].etag == stored.etag
    assert store.objects[key].content_type == "application/octet-stream"


def test_an_object_with_different_bytes_stops_and_names_the_key(tmp_path: Path) -> None:
    """A key that already holds other bytes is a new dataset id or a deliberate wipe."""
    store = MemoryObjectStore()
    _publish(tmp_path, store)
    key = "demo/images/00000000.jpg"
    stored = store.objects[key]
    store.objects[key] = replace(stored, body=stored.body + b"more")

    with pytest.raises(ObjectConflict, match="demo/images/00000000.jpg"):
        _publish(tmp_path, store)


def test_an_upload_that_does_not_read_back_is_a_verification_error(tmp_path: Path) -> None:
    """The bucket's answer after the write is what authorises the object."""

    class _MismatchedEtagStore(MemoryObjectStore):
        """A bucket whose readback answers a different entity tag from the one it stored."""

        def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
            etag = super().put(key, body, content_type=content_type, cache_control=cache_control)
            return f"{etag}-not-what-was-stored"

    with pytest.raises(VerificationError, match="demo/images/00000000.jpg"):
        _publish(tmp_path, _MismatchedEtagStore())


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


class _HeadDenied(MemoryObjectStore):
    """A bucket that grants PutObject but not GetObject: every HEAD answers 403."""

    def head(self, key: str) -> ObjectInfo | None:
        raise ObjectUnreadable(f"{key}: denied")


class _RaceStore(MemoryObjectStore):
    """A bucket where the create is refused because the key appeared while it was in flight."""

    def put(self, key: str, body: bytes, *, content_type: str, cache_control: str) -> str:
        if key not in self.objects:
            self.objects[key] = StoredObject(
                bytes(body), content_type, cache_control, "appeared-mid-flight"
            )
            raise AlreadyExists(f"{key}: lost the race")
        return super().put(key, body, content_type=content_type, cache_control=cache_control)


def test_a_bucket_that_denies_head_cannot_verify_an_upload(tmp_path: Path) -> None:
    """Without GetObject the write cannot be read back, so the run stops instead of guessing."""
    with pytest.raises(VerificationError, match="s3:GetObject"):
        _publish(tmp_path, _HeadDenied())


def test_an_occupied_key_that_cannot_be_read_is_a_conflict(tmp_path: Path) -> None:
    """A refused create is only a skip when the occupied object can be compared."""
    store = _HeadDenied()
    store.put(
        "demo/images/00000000.jpg",
        JPEG,
        content_type=CONTENT_TYPES["jpg"],
        cache_control=CACHE_CONTROL,
    )

    with pytest.raises(ObjectConflict, match="s3:GetObject"):
        _publish(tmp_path, store)


def test_losing_the_conditional_create_race_falls_back_to_comparison(tmp_path: Path) -> None:
    """A create refused because the key appeared mid-flight is compared, not failed."""
    summary = _publish(tmp_path, _RaceStore())

    assert summary.uploaded == 0  # type: ignore[attr-defined]
    assert summary.skipped == 2  # type: ignore[attr-defined]


def test_a_name_without_a_shard_number_is_not_checked() -> None:
    """Nothing offline can say how many files of an unnumbered shape there should be."""
    assert missing_shards([Path("data/part.parquet")]) == []


def test_a_complete_selection_has_nothing_missing() -> None:
    """The whole of every split is what a publishable cache is."""
    files = [
        Path("data/test-00000-of-00001.parquet"),
        Path("data/train-00000-of-00002.parquet"),
        Path("data/train-00001-of-00002.parquet"),
    ]

    assert missing_shards(files) == []


def test_an_incomplete_selection_names_the_missing_shard(tmp_path: Path) -> None:
    """A gap in the shards would give every later row an address that is not its own."""
    cache_dir = tmp_path / "cache"
    data = cache_dir / "demo" / "data"
    data.mkdir(parents=True)
    table = pa.Table.from_pylist([_record(JPEG, "a.JPG")], schema=_schema())
    pq.write_table(table, data / "train-00001-of-00002.parquet")

    with pytest.raises(IncompleteCache, match="train-00000-of-00002"):
        _publish(tmp_path, None, cache_dir=cache_dir)


def test_a_dry_run_refuses_an_incomplete_selection_too(tmp_path: Path) -> None:
    """The plan a dry run prints is only honest if the cache it would read is whole."""
    cache_dir = tmp_path / "cache"
    data = cache_dir / "demo" / "data"
    data.mkdir(parents=True)
    table = pa.Table.from_pylist([_record(JPEG, "a.JPG")], schema=_schema())
    pq.write_table(table, data / "train-00000-of-00002.parquet")

    with pytest.raises(IncompleteCache, match="train-00001-of-00002"):
        _publish(tmp_path, None, cache_dir=cache_dir, dry_run=True)


def test_a_limit_publishes_only_the_first_rows(tmp_path: Path) -> None:
    """A rehearsal publishes the dataset's prefix, not a slice of one file."""
    store = MemoryObjectStore()

    summary = _publish(tmp_path, store, limit=1)

    assert summary.rows == 1  # type: ignore[attr-defined]
    assert set(store.objects) == {"demo/images/00000000.jpg"}
    assert len(read_back(tmp_path / "index" / "demo.db")["rows"]) == 1


def test_a_full_publish_skips_the_prefix_a_rehearsal_wrote(tmp_path: Path) -> None:
    """Same keys, same bytes: the rehearsal does not touch the write-once rule."""
    store = MemoryObjectStore()
    _publish(tmp_path, store, limit=1)

    summary = _publish(tmp_path, store)

    assert summary.uploaded == 1  # type: ignore[attr-defined]
    assert summary.skipped == 1  # type: ignore[attr-defined]
    assert set(store.objects) == {"demo/images/00000000.jpg", "demo/images/00000001.png"}
