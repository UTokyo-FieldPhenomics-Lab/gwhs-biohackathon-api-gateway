"""Orchestrate a publish: build and validate the index, upload objects, verify.

One call per dataset performs the whole path: read the cached parquet serially, build the
metadata index beside its target, upload every image and mask as a single-part PUT, HEAD
each one back, and only then move the index into place. Nothing is uploaded before the
metadata validates, and a failure during upload leaves the previously published index
untouched. Reads stay serial; only uploads run concurrently.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from biohack_datasets import __version__
from biohack_datasets.adapters import Adapter, get_adapter
from biohack_datasets.formats import CACHE_CONTROL, CONTENT_TYPES
from biohack_datasets.index import build, commit, counts, utc_timestamp
from biohack_datasets.model import Row
from biohack_datasets.s3 import ObjectInfo, ObjectStore
from biohack_datasets.sources import Source, Upstream

DEFAULT_INDEX_DIR = Path.home() / ".config" / "biohack-gateway" / "index"
"""Host state directory the gateway reads the index from, beside env and tokens.json."""

DEFAULT_CONCURRENCY = 16
"""Uploads in flight when the caller names no other number."""

Emit = Callable[[str], None]


class PublishError(RuntimeError):
    """A publish cannot continue; the message names what stopped it."""


class MissingCache(PublishError):
    """The cached parquet a publish reads is absent."""


class ObjectConflict(PublishError):
    """An object under a key we would write holds different bytes."""


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
    uploaded, skipped, repaired : int
        Objects written, already correct, and re-put to repair a header.
    """

    dataset_id: str
    revision: str
    rules: str
    license: str | None
    rows: int
    objects: int
    uploaded: int
    skipped: int
    repaired: int


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


def publish_dataset(
    source: Source,
    *,
    cache_dir: Path,
    index_path: Path,
    bucket: str | None,
    store: ObjectStore | None,
    concurrency: int,
    dry_run: bool,
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
    AdapterNotImplemented
        The provenance record names an adapter this package does not implement.
    PublishError
        An object under a planned key holds different bytes.
    VerificationError
        An uploaded object does not read back as written.
    """
    if source.upstream is None:
        emit(f"{source.dataset_id}: skipped, no upstream revision pinned yet")
        return None
    if source.adapter is None:
        raise PublishError(f"{source.dataset_id}: pinned upstream declares no adapter")
    adapter = get_adapter(source.adapter)

    files = parquet_files(cache_dir, source.dataset_id, source.upstream)
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

    temporary = build(
        source,
        adapter.iter_rows(files),
        index_path,
        rules=__version__,
        generated_at=utc_timestamp(),
        mask_kind=adapter.mask_kind,
    )
    try:
        if store is None:
            emit("    uploaded nothing: no bucket configured")
            uploaded = skipped = repaired = 0
        else:
            uploaded, skipped, repaired = _upload(
                adapter, files, source.dataset_id, store, concurrency
            )
            emit(f"    {uploaded} uploaded, {repaired} repaired, {skipped} skipped")
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
        repaired=repaired,
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
            emit=emit,
        )
        if summary is not None:
            license_name = summary.license or "not cleared"
            emit(
                f"{dataset_id}: revision {summary.revision}, rules {summary.rules},"
                f" license {license_name}, rows {summary.rows}, objects {summary.objects}"
            )
    return 0


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


def _upload(
    adapter: Adapter,
    files: Sequence[Path],
    dataset_id: str,
    store: ObjectStore,
    concurrency: int,
) -> tuple[int, int, int]:
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

    Returns
    -------
    tuple of (int, int, int)
        Uploaded, skipped, and repaired object counts.
    """
    counts_by_action = {"uploaded": 0, "skipped": 0, "repaired": 0}
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending: deque[Future[str]] = deque()
        for row in adapter.iter_rows(files):
            for key, body, extension in _objects_of(dataset_id, row):
                pending.append(pool.submit(_put_object, store, key, body, extension))
                if len(pending) >= concurrency:
                    counts_by_action[pending.popleft().result()] += 1
        while pending:
            counts_by_action[pending.popleft().result()] += 1
    return counts_by_action["uploaded"], counts_by_action["skipped"], counts_by_action["repaired"]


def _put_object(store: ObjectStore, key: str, body: bytes, extension: str) -> str:
    """Put one object if it is absent or its owned headers changed, then verify it.

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
        `"uploaded"`, `"skipped"`, or `"repaired"`.

    Raises
    ------
    ObjectConflict
        The key holds a different number of bytes; the operator decides between a new
        dataset id and a deliberate wipe.
    VerificationError
        The object does not read back as written.
    """
    content_type = CONTENT_TYPES[extension]
    existing = store.head(key)
    if existing is None:
        etag = store.put(key, body, content_type=content_type, cache_control=CACHE_CONTROL)
        action = "uploaded"
    elif _already_correct(existing, body, content_type):
        etag = existing.etag
        action = "skipped"
    elif existing.size != len(body):
        raise ObjectConflict(
            f"{key}: bucket holds {existing.size} bytes, this publish would write {len(body)};"
            " a changed object is a new dataset id or a deliberate wipe, never an overwrite"
        )
    else:
        etag = store.put(key, body, content_type=content_type, cache_control=CACHE_CONTROL)
        action = "repaired"
    _verify(store, key, len(body), etag, content_type)
    return action


def _already_correct(existing: ObjectInfo, body: bytes, content_type: str) -> bool:
    """Say whether an existing object is already what this run would write.

    Parameters
    ----------
    existing : ObjectInfo
        What the bucket holds.
    body : bytes
        Bytes this run would write.
    content_type : str
        Type this run would write.

    Returns
    -------
    bool
        True when size and both owned headers already agree.
    """
    return (
        existing.size == len(body)
        and existing.content_type == content_type
        and existing.cache_control == CACHE_CONTROL
    )


def _verify(store: ObjectStore, key: str, size: int, etag: str, content_type: str) -> None:
    """Read one object back and compare it with what was just written.

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
        The object is missing or disagrees in size, entity tag, or a header.
    """
    readback = store.head(key)
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
