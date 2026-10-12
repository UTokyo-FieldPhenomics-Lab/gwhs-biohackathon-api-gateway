"""Reader for ``datasets/sources.yaml``, the provenance record of every upstream.

The YAML file is the single place that records which immutable upstream revision a dataset
is built from and which of that revision's files are consumed. Nothing here touches the
network; :mod:`biohack_datasets.fetch_cache` is the only reader, and the publisher will be
the next one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SOURCES_PATH = Path(__file__).resolve().parents[2] / "sources.yaml"
"""Provenance record shipped next to this package, ``datasets/sources.yaml``."""

DEFAULT_INCLUDE = ("data/*.parquet",)
"""What a dataset fetches unless it names its own selection: parquet under ``data/``."""

_COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Upstream:
    """One immutable upstream revision, and the files consumed from it.

    Attributes
    ----------
    repo : str
        Hub repository id, for example ``GlobalWheat/GWFSS_v1.0``.
    revision : str
        Full commit sha. A branch or tag is rejected, because a published address would
        otherwise move under us.
    include : tuple of str
        Glob patterns matched against repository-relative paths, selecting what to fetch.
        They never match the archives and cards sitting at the repository root.
    """

    repo: str
    revision: str
    include: tuple[str, ...]


@dataclass(frozen=True)
class Source:
    """One dataset id, and its upstream once that release has been pinned.

    Attributes
    ----------
    dataset_id : str
        Id that every published address of this data carries.
    upstream : Upstream or None
        ``None`` while a release is not pinned yet; every consumer skips such a dataset.
    """

    dataset_id: str
    upstream: Upstream | None


def load_sources(path: Path = SOURCES_PATH) -> dict[str, Source]:
    """Read the provenance record.

    Parameters
    ----------
    path : pathlib.Path, optional
        YAML file to read. Defaults to :data:`SOURCES_PATH`.

    Returns
    -------
    dict of str to Source
        One entry per dataset id, in file order.

    Raises
    ------
    ValueError
        An ``upstream`` is present but is not ``<repo>@<40-character commit sha>``.
    """
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        dataset_id: _parse_source(dataset_id, entry)
        for dataset_id, entry in document["datasets"].items()
    }


def _parse_source(dataset_id: str, entry: dict[str, Any]) -> Source:
    """Turn one entry of the provenance record into a :class:`Source`.

    Parameters
    ----------
    dataset_id : str
        Key the entry was filed under, repeated into the result.
    entry : dict
        The entry itself.

    Returns
    -------
    Source
        The parsed dataset.

    Raises
    ------
    ValueError
        The entry names an upstream that is not a repository at a commit sha.
    """
    declared = entry.get("upstream")
    if declared is None:
        return Source(dataset_id, None)

    repo, _, revision = str(declared).partition("@")
    if not repo or not _COMMIT.fullmatch(revision):
        raise ValueError(
            f"{dataset_id}: upstream must be '<repo>@<40-character commit sha>', got {declared!r}"
        )

    declared_include = entry.get("include")
    patterns = DEFAULT_INCLUDE if declared_include is None else tuple(declared_include)
    return Source(dataset_id, Upstream(repo, revision, patterns))
