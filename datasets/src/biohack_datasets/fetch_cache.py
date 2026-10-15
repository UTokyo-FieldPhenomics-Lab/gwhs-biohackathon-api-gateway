"""Materialise the pinned upstream files into the local cache.

The cache is machine-local and disposable: one command rebuilds it from
:mod:`biohack_datasets.sources`, and it holds upstream files verbatim, so the publisher
reads plain parquet paths and never talks to the Hub. Downloading, resuming, retrying,
pattern matching, and size checking are the Hub client's job; this module decides *what* to
fetch, reports the plan it is about to execute, and checks that what landed is readable
parquet.

The cache lives at ``datasets/.hf_cache/<dataset_id>/`` and is ignored by git.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pyarrow
import pyarrow.parquet as pq
from huggingface_hub import snapshot_download
from huggingface_hub.file_download import DryRunFileInfo
from huggingface_hub.utils.tqdm import disable_progress_bars

from biohack_datasets.sources import Source, Upstream

DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".hf_cache"
"""``datasets/.hf_cache``, resolved from the package so the working directory does not matter.

Named after the tool that fills it, like ``.mypy_cache``, ``.pytest_cache``, and ``.ruff_cache``.
"""

DEFAULT_CONCURRENCY = 16
"""Parallel downloads when the caller names no other number."""


class CacheVerificationError(RuntimeError):
    """A file in the cache is unreadable, so the run must not be treated as a good cache."""


def plan(upstream: Upstream, local_dir: Path) -> list[DryRunFileInfo]:
    """List the files an upstream puts in the cache, without downloading them.

    Parameters
    ----------
    upstream : Upstream
        Repository, pinned revision, and the patterns selecting its files.
    local_dir : pathlib.Path
        Cache directory of one dataset.

    Returns
    -------
    list of DryRunFileInfo
        One entry per selected file, carrying its upstream size and whether the cache
        already holds a copy.

    Notes
    -----
    A listing is not progress, so the bar the Hub client raises for every fetch is silenced
    here; the download itself keeps it.
    """
    with disable_progress_bars():
        files = snapshot_download(
            upstream.repo,
            repo_type="dataset",
            revision=upstream.revision,
            allow_patterns=list(upstream.include),
            local_dir=local_dir,
            dry_run=True,
        )
    return list(files)


def download(upstream: Upstream, local_dir: Path, concurrency: int) -> None:
    """Download the selected files into the cache.

    Parameters
    ----------
    upstream : Upstream
        Repository, pinned revision, and the patterns selecting its files.
    local_dir : pathlib.Path
        Cache directory of one dataset.
    concurrency : int
        Parallel downloads.

    Notes
    -----
    The Hub client skips files already present and intact, so a repeated run is a no-op and
    an interrupted run resumes rather than restarts. It answers with the directory it wrote,
    which is why the files to verify come from :func:`plan` rather than from this call.
    """
    snapshot_download(
        upstream.repo,
        repo_type="dataset",
        revision=upstream.revision,
        allow_patterns=list(upstream.include),
        local_dir=local_dir,
        max_workers=concurrency,
    )


def verify(paths: Sequence[Path]) -> dict[Path, int]:
    """Read back every fetched parquet file and report its row count.

    Parameters
    ----------
    paths : sequence of pathlib.Path
        Local files, taken from the plan that produced them.

    Returns
    -------
    dict of pathlib.Path to int
        Row count per parquet file. Anything else in ``paths`` is skipped: it was selected
        deliberately, and the Hub client already checked its size.

    Raises
    ------
    CacheVerificationError
        At least one parquet file is unreadable. Every such file is deleted first, so the
        next run refetches it rather than trusting a cache entry marked as present.

    Notes
    -----
    Sizes are checked while downloading; what the Hub client cannot check is that the bytes
    are a parquet file at all, which is the one thing the publisher needs.
    """
    rows: dict[Path, int] = {}
    unreadable: list[Path] = []
    for path in paths:
        if path.suffix != ".parquet":
            continue
        try:
            rows[path] = pq.ParquetFile(path).metadata.num_rows
        except (pyarrow.ArrowException, OSError):
            unreadable.append(path)

    if unreadable:
        for path in unreadable:
            path.unlink(missing_ok=True)
        raise CacheVerificationError(
            "unreadable parquet, deleted for refetch: "
            + ", ".join(str(path) for path in unreadable)
        )
    return rows


def fetch_datasets(
    sources: dict[str, Source],
    dataset_ids: Sequence[str],
    *,
    cache_dir: Path,
    dry_run: bool,
    concurrency: int,
    emit: Callable[[str], None] = print,
) -> int:
    """Fetch every selected dataset into the cache.

    Parameters
    ----------
    sources : dict of str to Source
        The provenance record.
    dataset_ids : sequence of str
        Datasets to handle; empty means every dataset with a pinned upstream.
    cache_dir : pathlib.Path
        Root of the cache. It is resolved so the plan's paths are absolute even when the
        caller passes a relative directory.
    dry_run : bool
        Report the plan and download nothing.
    concurrency : int
        Parallel downloads.
    emit : callable, optional
        Report sink, one line at a time.

    Returns
    -------
    int
        Zero when every selected dataset is cached and verifies; one when a file in the
        cache is unreadable.
    """
    root = cache_dir.expanduser().resolve()
    selected = list(dataset_ids) if dataset_ids else sorted(sources)
    for dataset_id in selected:
        _fetch_one(sources[dataset_id], root, dry_run=dry_run, concurrency=concurrency, emit=emit)
    return 0


def _fetch_one(
    source: Source,
    cache_dir: Path,
    *,
    dry_run: bool,
    concurrency: int,
    emit: Callable[[str], None],
) -> None:
    """Report, and unless dry-running fetch, the files of one dataset.

    Parameters
    ----------
    source : Source
        Dataset to handle; one without a pinned revision is reported and skipped.
    cache_dir : pathlib.Path
        Root of the cache, holding one directory per dataset.
    dry_run : bool
        Report the plan and download nothing.
    concurrency : int
        Parallel downloads.
    emit : callable
        Report sink.
    """
    if source.upstream is None:
        emit(f"{source.dataset_id}: skipped, no upstream revision pinned yet")
        return

    local_dir = cache_dir / source.dataset_id
    files = plan(source.upstream, local_dir)
    to_download = [file for file in files if file.will_download]
    planned = sum(file.file_size or 0 for file in files)
    emit(
        f"{source.dataset_id}: {len(files)} selected, {_size(planned)} total, "
        f"{len(to_download)} to download -> {local_dir}"
    )
    for file in files:
        state = "cached" if file.is_cached else "to download"
        emit(f"    {file.filename}  {_size(file.file_size or 0)}  {state}")

    if dry_run:
        return

    download(source.upstream, local_dir, concurrency)
    for path, rows in verify([Path(file.local_path) for file in files]).items():
        emit(f"    {path.relative_to(local_dir)}  {rows} rows")


def _size(nbytes: int) -> str:
    """Format a byte count for the run report, in decimal units.

    Parameters
    ----------
    nbytes : int
        Size in bytes.

    Returns
    -------
    str
        Human-readable size, in the same units the Hub itself reports.

    Notes
    -----
    The Hub's own formatter lives in its private CLI package, so it is not reusable here.
    """
    if nbytes >= 1_000_000_000:
        return f"{nbytes / 1e9:.2f} GB"
    if nbytes >= 1_000_000:
        return f"{nbytes / 1e6:.1f} MB"
    return f"{nbytes} B"
