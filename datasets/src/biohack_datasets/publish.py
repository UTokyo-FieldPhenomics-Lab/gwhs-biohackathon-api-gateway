"""Orchestrate a publish: build and validate the index, upload objects, verify.

One call per dataset performs the whole path: read the cached parquet serially, build the
metadata index beside its target, upload every image and mask as a single-part PUT, HEAD
each one back, and only then move the index into place. Nothing is uploaded before the
metadata validates, and a failure during upload leaves the previously published index
untouched. Reads stay serial; only uploads run concurrently.
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
from biohack_datasets.s3 import AlreadyExists, ObjectInfo, ObjectStore, ObjectUnreadable
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
        An object under a planned key holds different bytes.
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
            uploaded = skipped = repaired = 0
        else:
            uploaded, skipped, repaired = _upload(
                adapter, files, source.dataset_id, store, concurrency, limit=limit
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
        for row in _rows(adapter, files, limit):
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
    try:
        existing = store.head(key)
    except ObjectUnreadable:
        # A private bucket cannot say "absent" without `ListBucket`, so existence is settled
        # by a conditional create, which succeeds only if the key is really free.
        existing = None
    if existing is None:
        try:
            etag = store.put(
                key,
                body,
                content_type=content_type,
                cache_control=CACHE_CONTROL,
                if_none_match=True,
            )
        except AlreadyExists:
            action, etag = _settle_existing(store, key, body, content_type)
        else:
            action = "uploaded"
    else:
        action, etag = _settle_existing(store, key, body, content_type, existing)
    _verify(store, key, len(body), etag, content_type)
    return action


def _settle_existing(
    store: ObjectStore,
    key: str,
    body: bytes,
    content_type: str,
    existing: ObjectInfo | None = None,
) -> tuple[str, str]:
    """Decide what to do with a key that already holds an object.

    Parameters
    ----------
    store : ObjectStore
        Destination bucket.
    key : str
        Object key.
    body : bytes
        Bytes this run would write.
    content_type : str
        Type this run would write.
    existing : ObjectInfo, optional
        What the bucket already reported. Read afresh when omitted, which is the case after
        a conditional create lost the race.

    Returns
    -------
    tuple of (str, str)
        The action, and the entity tag of the object as it now stands.

    Raises
    ------
    ObjectConflict
        The key holds a different number of bytes, or it exists while `GetObject` is denied,
        so the comparison cannot be made.
    """
    if existing is None:
        try:
            existing = store.head(key)
        except ObjectUnreadable as error:
            raise ObjectConflict(
                f"{key}: the object exists but HEAD is denied;"
                " the policy needs s3:GetObject on the prefix"
            ) from error
    if existing is None:
        raise ObjectConflict(
            f"{key}: a conditional create was refused but the key reads back as absent"
        )
    if _already_correct(existing, body, content_type):
        return "skipped", existing.etag
    if existing.size != len(body):
        raise ObjectConflict(
            f"{key}: bucket holds {existing.size} bytes, this publish would write {len(body)};"
            " a changed object is a new dataset id or a deliberate wipe, never an overwrite"
        )
    etag = store.put(key, body, content_type=content_type, cache_control=CACHE_CONTROL)
    return "repaired", etag


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
