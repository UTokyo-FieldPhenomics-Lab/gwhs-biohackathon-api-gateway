"""Command-line entry point for the dataset publisher.

One installed command with two subcommands, the shape biohack-gateway tokens already uses:
fetch fills the local upstream cache from the pinned revisions, publish turns that cache
into a metadata index and writes the image objects to S3. Neither command reads a
configuration file: the environment carries the bucket, the region, and the path to the
console's access-key CSV, and the host keeps them in a file the operator sources.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from biohack_datasets.adapters import AdapterNotImplemented, MissingVocabulary
from biohack_datasets.credentials import CredentialError, read_access_keys_csv
from biohack_datasets.fetch_cache import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CONCURRENCY,
    CacheFetchError,
    CacheVerificationError,
    fetch_datasets,
)
from biohack_datasets.publish import (
    DEFAULT_INDEX_DIR,
    PublishError,
    parse_bucket,
    publish_datasets,
)
from biohack_datasets.s3 import Boto3ObjectStore, session_from_access_keys
from biohack_datasets.sources import Source, load_sources


def main(argv: list[str] | None = None) -> int:
    """Run the biohack-datasets CLI.

    Parameters
    ----------
    argv : list of str, optional
        Argument list to parse, excluding the program name. Defaults to sys.argv[1:].

    Returns
    -------
    int
        Zero on success; one when a dataset cannot be fetched or published. A non-zero
        exit is the only signal that stops the pipeline.
    """
    sources = load_sources()
    args = _build_parser(sorted(sources)).parse_args(argv)
    try:
        if args.command == "fetch":
            return fetch_datasets(
                sources,
                args.dataset or [],
                cache_dir=_cache_dir(args),
                dry_run=args.dry_run,
                concurrency=args.concurrency,
            )
        return _publish(args, sources)
    except (
        AdapterNotImplemented,
        CacheFetchError,
        CacheVerificationError,
        CredentialError,
        MissingVocabulary,
        PublishError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


def _publish(args: argparse.Namespace, sources: dict[str, Source]) -> int:
    """Run the publish subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed switches.
    sources : dict of str to Source
        The provenance record.

    Returns
    -------
    int
        Process exit code.
    """
    bucket = parse_bucket(args.bucket or os.environ.get("BIOHACK_BUCKET"))
    index_dir = Path(args.index_dir).expanduser() if args.index_dir else DEFAULT_INDEX_DIR
    store = None
    if not args.dry_run and bucket is not None:
        store = Boto3ObjectStore(bucket, session=_publish_session())
    return publish_datasets(
        sources,
        args.dataset or [],
        cache_dir=_cache_dir(args),
        index_dir=index_dir,
        bucket=bucket,
        store=store,
        concurrency=args.concurrency,
        dry_run=args.dry_run,
        limit=args.limit,
    )


def _publish_session() -> Any:
    """Return the boto3 session a publish authenticates with.

    Returns
    -------
    boto3.Session or None
        A session built from the key pair in `BIOHACK_ACCESS_KEYS_CSV`, the console's
        download; `None` when that variable is unset, so boto3's standard chain applies and
        a laptop can use its own profile.

    Raises
    ------
    CredentialError
        The variable names something that is not the console's two-column CSV.
    """
    path = os.environ.get("BIOHACK_ACCESS_KEYS_CSV")
    if not path:
        return None
    return session_from_access_keys(read_access_keys_csv(Path(path).expanduser()))


def _cache_dir(args: argparse.Namespace) -> Path:
    """Resolve the cache directory named on the command line.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed switches carrying an optional cache_dir.

    Returns
    -------
    pathlib.Path
        The requested directory, or the package default.
    """
    return Path(args.cache_dir) if args.cache_dir else DEFAULT_CACHE_DIR


def _positive(text: str) -> int:
    """Parse a row count that must be at least one.

    Parameters
    ----------
    text : str
        The value a caller passed on the command line.

    Returns
    -------
    int
        The count.

    Raises
    ------
    argparse.ArgumentTypeError
        The value is not an integer of at least one. argparse reports it as a usage error, rather
        than letting a negative count reach islice.
    """
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a count of at least 1, got {text!r}")
    return value


def _build_parser(dataset_ids: list[str]) -> argparse.ArgumentParser:
    """Build the two-subcommand parser.

    Parameters
    ----------
    dataset_ids : list of str
        Dataset ids accepted by --dataset, so a typo fails before anything runs.

    Returns
    -------
    argparse.ArgumentParser
        The parser.
    """
    parser = argparse.ArgumentParser(prog="biohack-datasets", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch = subcommands.add_parser(
        "fetch",
        help="download the pinned upstream files into the local cache",
        description="Download the pinned upstream files of each dataset; nothing is published.",
    )
    _add_common(fetch, dataset_ids)
    fetch.add_argument(
        "--cache-dir",
        help=f"where the cache is written; default {DEFAULT_CACHE_DIR}",
    )

    publish = subcommands.add_parser(
        "publish",
        help="build the metadata index and upload the image objects",
        description=(
            "Build, validate, upload, and verify one dataset. A publish never downloads: "
            "run biohack-datasets fetch first."
        ),
    )
    _add_common(publish, dataset_ids)
    publish.add_argument(
        "--cache-dir",
        help=f"where the upstream parquet is read from; default {DEFAULT_CACHE_DIR}",
    )
    publish.add_argument(
        "--bucket",
        help="target bucket, s3://NAME; default $BIOHACK_BUCKET, else build locally",
    )
    publish.add_argument(
        "--limit",
        type=_positive,
        metavar="N",
        help=(
            "publish only the first N rows: a rehearsal, whose objects a full publish then skips. "
            "The index describes N rows, so point --index-dir at a throwaway directory"
        ),
    )
    publish.add_argument(
        "--index-dir",
        help=f"where <dataset>.db is written; default {DEFAULT_INDEX_DIR}",
    )
    return parser


def _add_common(parser: argparse.ArgumentParser, dataset_ids: list[str]) -> None:
    """Add the switches both subcommands share.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        Subcommand parser to extend.
    dataset_ids : list of str
        Dataset ids accepted by --dataset.
    """
    parser.add_argument(
        "--dataset",
        nargs="+",
        choices=dataset_ids,
        metavar="ID",
        help="datasets to handle; omit for every dataset with a pinned upstream",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan, write nothing, upload nothing",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"parallel operations; default {DEFAULT_CONCURRENCY}",
    )


if __name__ == "__main__":
    raise SystemExit(main())
