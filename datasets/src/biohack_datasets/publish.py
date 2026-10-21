"""Orchestrate a publish: build and validate the index, upload objects, verify.

One call per dataset performs the whole path: read the cached parquet serially, build the
metadata index beside its target, create every image and mask with a conditional single-part
PUT, HEAD each new object back, and only then move the index into place. Nothing is uploaded
before the metadata validates, and a failure during upload leaves the previously published
index untouched. Reads stay serial; only uploads run concurrently.

An object is written once. A key that is already taken is compared and then either skipped
or reported as a conflict; it is never overwritten and its headers are never repaired by a
second write. The comparison establishes size and the two owned headers, which is all a
bucket will say about an object without transferring it.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import pyarrow.parquet as pq

from biohack_datasets import __version__
from biohack_datasets.adapters import Adapter, check_adapter, get_adapter
from biohack_datasets.fetch_cache import labels_path
from biohack_datasets.formats import CACHE_CONTROL, CONTENT_TYPES
from biohack_datasets.index import build, commit, counts, utc_timestamp
from biohack_datasets.labels import Palette, load_palette
from biohack_datasets.model import Row
from biohack_datasets.s3 import AlreadyExists, ObjectStore, ObjectUnreadable
from biohack_datasets.sources import Source, Upstream

DEFAULT_INDEX_DIR = Path.home() / ".config" / "biohack-gateway" / "index"
"""Host state directory the gateway reads the index from, beside env and tokens.json."""

DEFAULT_CONCURRENCY = 16
"""Uploads in flight when the caller names no other number."""

Emit = Callable[[str], None]

_SHARD_NAME = re.compile(r"^(?P<split>.+)-(?P<index>\d+)-of-(?P<total>\d+)\.parquet$")
"""A sharded parquet name as the Hub writes one, such as train-00000-of-00002.parquet."""


class PublishError(RuntimeError):
    """A publish cannot continue; the message names what stopped it."""


class MissingCache(PublishError):
    """The cached parquet a publish reads is absent."""


class IncompleteCache(PublishError):
    """The cached selection is missing a shard, so its row numbers would not be the dataset's."""


class ObjectConflict(PublishError):
    """An object already occupies a key this publish would write, and does not match."""


class VerificationError(PublishError):
    """An object does not read back as it was just written."""


@dataclass(frozen=True)
class PublishSummary:
    """What one dataset's publish did.

    Attributes
    ----------
    dataset_id : str
        Dataset published.
    revision : str
        Pinned upstream revision the index records.
    rules : str
        Installed publisher version.
    license : str or None
        Distribution terms, absent until cleared.
    rows : int
        Images indexed.
    objects : int
        Object annotations indexed.
    uploaded, skipped : int
        Objects created by this run, and objects already present with matching size and
        owned headers.
    """

    dataset_id: str
    revision: str
    rules: str
    license: str | None
    rows: int
    objects: int
    uploaded: int
    skipped: int


def parse_bucket(value: str | None) -> str | None:
    """Turn a bucket setting into a bare bucket name.

    Parameters
    ----------
    value : str or None
        `s3://name`, `name`, or `None`.

    Returns
    -------
    str or None
        The bucket name, or `None` when nothing was configured.
    """
    if value is None:
        return None
    name = value.strip()
    if name.startswith("s3://"):
        name = name[len("s3://") :]
    return name.rstrip("/") or None


def parquet_files(cache_dir: Path, dataset_id: str, upstream: Upstream) -> list[Path]:
    """List a dataset's cached parquet files in the order that defines row numbers.

    Parameters
    ----------
    cache_dir : pathlib.Path
        Root of the cache.
    dataset_id : str
        Dataset whose directory is read.
    upstream : Upstream
        Supplies the selection patterns.

    Returns
    -------
    list of pathlib.Path
        Files matching the selection, sorted by repository-relative path.
    """
    root = cache_dir / dataset_id
    found: dict[str, Path] = {}
    for pattern in upstream.include:
        for path in root.glob(pattern):
            if path.is_file():
                found[str(path.relative_to(root))] = path
    return [found[name] for name in sorted(found)]


def missing_shards(files: Sequence[Path]) -> list[str]:
    """Name the shards missing from a cached selection, from the file names alone.

    A dataset's absolute row number is the cumulative index over its files in sorted path order, so
    a selection missing a shard does not merely publish fewer rows: every row after the gap takes
    the address of a row that is not there, and a later, complete publish meets its own different
    bytes at those keys. A shard's name carries the count, which is the one fact the check needs and
    the one thing a publish can read without the network.

    Parameters
    ----------
    files : sequence of pathlib.Path
        The cached files of one dataset, in address order.

    Returns
    -------
    list of str
        One file name per missing shard. A name that carries no shard number is skipped, because
        nothing offline can say how many files of that shape there should be.
    """
    present: dict[tuple[str, int], set[int]] = {}
    for path in files:
        match = _SHARD_NAME.match(path.name)
        if match is None:
            continue
        key = (match.group("split"), int(match.group("total")))
        present.setdefault(key, set()).add(int(match.group("index")))

    missing: list[str] = []
    for (split, total), indexes in sorted(present.items()):
        missing.extend(
            f"{split}-{index:05d}-of-{total:05d}.parquet"
            for index in range(total)
            if index not in indexes
        )
    return missing


def publish_dataset(
    source: Source,
    *,
    cache_dir: Path,
    index_path: Path,
    bucket: str | None,
    store: ObjectStore | None,
    concurrency: int,
    dry_run: bool,
    limit: int | None = None,
    emit: Emit = print,
) -> PublishSummary | None:
    """Build, validate, upload, and verify one dataset.

    Parameters
    ----------
    source : Source
        Dataset to publish; one without a pinned upstream is reported and skipped.
    cache_dir : pathlib.Path
        Root of the cached upstream parquet.
    index_path : pathlib.Path
        Final path of this dataset's index.
    bucket : str or None
        Bucket name, for the plan report only.
    store : ObjectStore or None
        Where objects go. `None` builds the index and uploads nothing.
    concurrency : int
        Uploads in flight.
    dry_run : bool
        Print the plan and write nothing.
    limit : int or None, optional
        Publish only the first limit rows. The objects are the prefix a later, full publish finds
        already correct and skips, while the index describes just those rows, so a rehearsal points
        index_path at a throwaway directory rather than the gateway's.
    emit : callable, optional
        Sink for the report, one line at a time.

    Returns
    -------
    PublishSummary or None
        What was done, or `None` for a dry run or a skipped dataset.

    Raises
    ------
    MissingCache
        The cache holds no parquet for the dataset.
    IncompleteCache
        The cache holds part of the selection rather than all of it.
    AdapterNotImplemented
        The provenance record names an adapter this package does not implement.
    PublishError
        An object already occupies a planned key with a different size or owned header.
    VerificationError
        An uploaded object does not read back as written.
    """
    if source.upstream is None:
        emit(f"{source.dataset_id}: skipped, no upstream revision pinned yet")
        return None
    if source.adapter is None:
        raise PublishError(f"{source.dataset_id}: pinned upstream declares no adapter")
    # The name is checked here so a typo fails before anything is read. The reader itself is built
    # further down, once the vocabulary is loaded, because the mask schema needs it to exist.
    check_adapter(source.adapter)

    files = parquet_files(cache_dir, source.dataset_id, source.upstream)
    if missing := missing_shards(files):
        raise IncompleteCache(
            f"{source.dataset_id}: the cache holds {len(files)} file(s) and is missing"
            f" {', '.join(missing)}; run 'biohack-datasets fetch --dataset {source.dataset_id}'"
        )
    upstream = source.upstream
    emit(
        f"{source.dataset_id}: adapter {source.adapter},"
        f" upstream {upstream.repo}@{upstream.revision}"
    )
    if files:
        cached = cache_dir / source.dataset_id
        emit(f"    cache {cached}: {len(files)} parquet file(s), {_cached_rows(files)} row(s)")
    else:
        emit(f"    cache {cache_dir / source.dataset_id}: empty (run biohack-datasets fetch)")
    if source.labels is not None:
        vocabulary = labels_path(cache_dir, source.dataset_id, source.labels)
        state = "cached" if vocabulary.is_file() else "missing (run biohack-datasets fetch)"
        emit(f"    vocabulary {source.labels.url} -> {vocabulary}  {state}")
    if limit is not None:
        emit(f"    limit {limit} row(s): a rehearsal, whose objects a full publish will skip")
    emit(f"    index {index_path}")
    emit(
        f"    bucket s3://{bucket}" if bucket else "    bucket none: build locally, upload nothing"
    )
    if dry_run:
        return None
    if not files:
        raise MissingCache(
            f"{source.dataset_id}: no cached parquet under {cache_dir / source.dataset_id};"
            f" run 'biohack-datasets fetch --dataset {source.dataset_id}'"
        )

    palette = _palette_of(source, cache_dir)
    adapter = get_adapter(source.adapter, palette)
    temporary = build(
        source,
        _rows(adapter, files, limit),
        index_path,
        rules=__version__,
        generated_at=utc_timestamp(),
        classes=palette.classes if palette is not None else (),
        mask_kind=adapter.mask_kind,
    )
    try:
        if store is None:
            emit("    uploaded nothing: no bucket configured")
            uploaded = skipped = 0
        else:
            uploaded, skipped = _upload(
                adapter, files, source.dataset_id, store, concurrency, limit=limit
            )
            emit(f"    {uploaded} uploaded, {skipped} skipped")
        rows, objects = counts(temporary)
        commit(temporary, index_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return PublishSummary(
        dataset_id=source.dataset_id,
        revision=source.upstream.revision,
        rules=__version__,
        license=source.license,
        rows=rows,
        objects=objects,
        uploaded=uploaded,
        skipped=skipped,
    )


def publish_datasets(
    sources: dict[str, Source],
    dataset_ids: Sequence[str],
    *,
    cache_dir: Path,
    index_dir: Path,
    bucket: str | None,
    store: ObjectStore | None,
    concurrency: int,
    dry_run: bool,
    limit: int | None = None,
    emit: Emit = print,
) -> int:
    """Publish every selected dataset, stopping at the first failure.

    Parameters
    ----------
    sources : dict of str to Source
        The provenance record.
    dataset_ids : sequence of str
        Datasets to handle; empty means every dataset with a pinned upstream.
    cache_dir : pathlib.Path
        Root of the cached upstream parquet.
    index_dir : pathlib.Path
        Directory the per-dataset indexes are written to.
    bucket : str or None
        Bucket name, for the report.
    store : ObjectStore or None
        Where objects go; `None` builds locally.
    concurrency : int
        Uploads in flight.
    dry_run : bool
        Print the plan and write nothing.
    limit : int or None, optional
        Publish only the first limit rows of each dataset.
    emit : callable, optional
        Report sink.

    Returns
    -------
    int
        Always `0`. A failure raises instead, so the caller reports it once.
    """
    selected = list(dataset_ids) if dataset_ids else sorted(sources)
    for dataset_id in selected:
        source = sources[dataset_id]
        summary = publish_dataset(
            source,
            cache_dir=cache_dir,
            index_path=index_dir / f"{dataset_id}.db",
            bucket=bucket,
            store=store,
            concurrency=concurrency,
            dry_run=dry_run,
            limit=limit,
            emit=emit,
        )
        if summary is not None:
            license_name = summary.license or "not cleared"
            emit(
                f"{dataset_id}: revision {summary.revision}, rules {summary.rules},"
                f" license {license_name}, rows {summary.rows}, objects {summary.objects}"
            )
    return 0


def _palette_of(source: Source, cache_dir: Path) -> Palette | None:
    """Read a dataset's pinned class vocabulary out of the cache.

    Parameters
    ----------
    source : Source
        Dataset being published.
    cache_dir : pathlib.Path
        Root of the cache.

    Returns
    -------
    Palette or None
        The vocabulary, or `None` when the dataset's source names none.

    Raises
    ------
    MissingCache
        The source names a vocabulary that has not been fetched. Publishing without it would build
        an index whose classes and row classes are silently empty, which reads as a dataset with no
        vocabulary rather than as a missing file.
    """
    if source.labels is None:
        return None
    path = labels_path(cache_dir, source.dataset_id, source.labels)
    if not path.is_file():
        raise MissingCache(
            f"{source.dataset_id}: no cached class vocabulary at {path};"
            f" run 'biohack-datasets fetch --dataset {source.dataset_id}'"
        )
    return load_palette(path)


def _cached_rows(files: Sequence[Path]) -> int:
    """Count the rows of the cached parquet files, from their footers only.

    Parameters
    ----------
    files : sequence of pathlib.Path
        Parquet files to count.

    Returns
    -------
    int
        Total rows across the files.
    """
    return sum(pq.ParquetFile(path).metadata.num_rows for path in files)


def _objects_of(dataset_id: str, row: Row) -> Iterator[tuple[str, bytes, str]]:
    """Yield the object key, body, and extension of one row.

    Parameters
    ----------
    dataset_id : str
        Prefix every key starts with.
    row : Row
        Neutral row to emit objects for.

    Yields
    ------
    tuple of (str, bytes, str)
        Object key, bytes, and extension.
    """
    yield f"{dataset_id}/images/{row.row:08d}.{row.image_ext}", row.image, row.image_ext
    if row.mask is not None and row.mask_ext is not None:
        yield f"{dataset_id}/masks/{row.row:08d}.{row.mask_ext}", row.mask, row.mask_ext


def _rows(adapter: Adapter, files: Sequence[Path], limit: int | None) -> Iterator[Row]:
    """Read a dataset's rows, either all of them or the first limit.

    Parameters
    ----------
    adapter : Adapter
        Reader of the dataset's schema.
    files : sequence of pathlib.Path
        Cached parquet files, in address order.
    limit : int or None
        How many rows to read, or None for every row.

    Returns
    -------
    Iterator of Row
        The rows, numbered from zero either way, so a limit yields the dataset's prefix.
    """
    rows = adapter.iter_rows(files)
    return rows if limit is None else islice(rows, limit)


def _upload(
    adapter: Adapter,
    files: Sequence[Path],
    dataset_id: str,
    store: ObjectStore,
    concurrency: int,
    limit: int | None = None,
) -> tuple[int, int]:
    """Upload every object, reading rows serially and uploading concurrently.

    Parameters
    ----------
    adapter : Adapter
        Reader of the dataset's schema.
    files : sequence of pathlib.Path
        Cached parquet files, in address order.
    dataset_id : str
        Prefix every key starts with.
    store : ObjectStore
        Destination bucket.
    concurrency : int
        Uploads in flight.
    limit : int or None, optional
        Read only the dataset's first rows.

    Returns
    -------
    tuple of (int, int)
        Uploaded and skipped object counts.
    """
    counts_by_action = {"uploaded": 0, "skipped": 0}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending: deque[Future[str]] = deque()
        for row in _rows(adapter, files, limit):
            for key, body, extension in _objects_of(dataset_id, row):
                pending.append(pool.submit(_put_object, store, key, body, extension))
                if len(pending) >= concurrency:
                    counts_by_action[pending.popleft().result()] += 1
        while pending:
            counts_by_action[pending.popleft().result()] += 1
    return counts_by_action["uploaded"], counts_by_action["skipped"]


def _put_object(store: ObjectStore, key: str, body: bytes, extension: str) -> str:
    """Create one object when the key is free, then verify it; a taken key is compared.

    The bucket is asked to create first, so no HEAD is ever read as evidence of absence. A
    create that is refused means the key already holds something: it is compared against this
    run's size and owned headers, and reported as a conflict when they disagree. A new object
    is read back before its key counts as published.

    Parameters
    ----------
    store : ObjectStore
        Destination bucket.
    key : str
        Object key.
    body : bytes
        Bytes to store.
    extension : str
        Sniffed extension, which decides the content type.

    Returns
    -------
    str
        `"uploaded"` for a new object, `"skipped"` for an occupied key that already matches.

    Raises
    ------
    ObjectConflict
        The occupied key holds a different size or a different owned header. The operator
        decides between a new dataset id and a deliberate wipe.
    VerificationError
        A new object does not read back as written.
    """
    content_type = CONTENT_TYPES[extension]
    try:
        etag = store.put(key, body, content_type=content_type, cache_control=CACHE_CONTROL)
    except AlreadyExists:
        _require_already_correct(store, key, len(body), content_type)
        return "skipped"
    _verify(store, key, len(body), etag, content_type)
    return "uploaded"


def _require_already_correct(store: ObjectStore, key: str, size: int, content_type: str) -> None:
    """Confirm that an occupied object's size and owned headers match the planned object.

    Size and the two owned headers are the whole comparison a bucket supports without
    transferring the object, so this establishes agreement on those and nothing more. It is
    not a claim that the stored bytes are identical, and it never writes.

    Parameters
    ----------
    store : ObjectStore
        Destination bucket.
    key : str
        Object key.
    size : int
        Byte length this run would write.
    content_type : str
        Type this run would write.

    Raises
    ------
    ObjectConflict
        The object cannot be read, holds a different number of bytes, or was stored with a
        different content type or cache lifetime.
    """
    try:
        existing = store.head(key)
    except ObjectUnreadable as error:
        raise ObjectConflict(
            f"{key}: the object exists but HEAD is denied, so it cannot be compared;"
            " the policy needs s3:GetObject on the prefix"
        ) from error
    if existing is None:
        raise ObjectConflict(
            f"{key}: a conditional create was refused but the key reads back as absent"
        )
    if existing.size != size:
        raise ObjectConflict(
            f"{key}: bucket holds {existing.size} bytes, this publish would write {size};"
            " a changed object is a new dataset id or a deliberate wipe, never an overwrite"
        )
    if existing.content_type != content_type or existing.cache_control != CACHE_CONTROL:
        raise ObjectConflict(
            f"{key}: bucket holds {existing.content_type!r} with Cache-Control"
            f" {existing.cache_control!r}, this publish would write {content_type!r} with"
            f" {CACHE_CONTROL!r}; a header is not repaired by a second write"
        )


def _verify(store: ObjectStore, key: str, size: int, etag: str, content_type: str) -> None:
    """Read one new object back and compare it with what was just written.

    Parameters
    ----------
    store : ObjectStore
        Bucket written to.
    key : str
        Object key.
    size : int
        Byte length sent.
    etag : str
        Entity tag the write answered with.
    content_type : str
        Type sent.

    Raises
    ------
    VerificationError
        The object is missing, cannot be read, or disagrees in size, entity tag, or a header.
    """
    try:
        readback = store.head(key)
    except ObjectUnreadable as error:
        raise VerificationError(
            f"{key}: the write succeeded but HEAD is denied, so it cannot be verified;"
            " the policy needs s3:GetObject on the prefix"
        ) from error
    if (
        readback is None
        or readback.size != size
        or readback.etag != etag
        or readback.content_type != content_type
        or readback.cache_control != CACHE_CONTROL
    ):
        raise VerificationError(
            f"{key}: read back {readback!r}, expected {size} bytes with ETag {etag!r}"
        )
