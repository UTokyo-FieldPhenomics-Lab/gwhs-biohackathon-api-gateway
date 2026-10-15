"""Reader for ``datasets/sources.yaml``, the provenance record of every upstream.

The YAML file is the single place that records which immutable upstream revision a dataset
is built from, which of that revision's files are consumed, which adapter reads its schema,
and under which license it may be distributed. Nothing here touches the network;
:mod:`biohack_datasets.fetch_cache` and :mod:`biohack_datasets.publish` are its consumers.
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
class Labels:
    """A pinned upstream class vocabulary, recorded as ``<repo>@<sha>#<path>``.

    The palette and the class names of a mask annotation are published by an upstream
    repository next to its dataset. Reading them from here, rather than from a constant in
    code, is what makes the mapping provenance: it can be re-derived from the record.

    Attributes
    ----------
    repo : str
        Hub repository that publishes the vocabulary.
    revision : str
        Full commit sha of that repository.
    path : str
        File inside the repository, for example ``class.json``.
    """

    repo: str
    revision: str
    path: str

    def __str__(self) -> str:
        """Return the reference in the form the record writes it.

        Returns
        -------
        str
            ``<repo>@<sha>#<path>``.
        """
        return f"{self.repo}@{self.revision}#{self.path}"


@dataclass(frozen=True)
class Source:
    """One dataset id, and its upstream once that release has been pinned.

    Attributes
    ----------
    dataset_id : str
        Id that every published address of this data carries.
    upstream : Upstream or None
        ``None`` while a release is not pinned yet; every consumer skips such a dataset.
    adapter : str or None
        Reader that understands this dataset's schema. It names a schema rather than a
        dataset, so ``gwhs`` and ``gwhs-temp`` share one. Required with a pinned upstream.
    license : str or None
        Distribution terms, ``None`` until they are cleared. A rehearsal may publish with
        ``None``; public distribution may not.
    labels : Labels or None
        Pinned upstream class vocabulary, where a mask annotation has one.
    """

    dataset_id: str
    upstream: Upstream | None
    adapter: str | None = None
    license: str | None = None
    labels: Labels | None = None


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
        An ``upstream`` is not ``<repo>@<40-character commit sha>``, a pinned upstream
        names no ``adapter``, or a ``labels`` reference is not
        ``<repo>@<40-character commit sha>#<path>``.
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
        The entry declares a malformed upstream, adapter, license, or labels reference.
    """
    adapter = _optional_text(dataset_id, "adapter", entry.get("adapter"))
    declared_license = _optional_text(dataset_id, "license", entry.get("license"))
    labels = _parse_labels(dataset_id, entry.get("labels"))
    declared = entry.get("upstream")
    if declared is None:
        return Source(dataset_id, None, adapter, declared_license, labels)
    if adapter is None:
        raise ValueError(f"{dataset_id}: a pinned upstream needs an 'adapter' naming its reader")
    upstream = _parse_upstream(dataset_id, declared, entry.get("include"))
    return Source(dataset_id, upstream, adapter, declared_license, labels)


def _optional_text(dataset_id: str, field: str, declared: Any) -> str | None:
    """Read an optional free-text field.

    Parameters
    ----------
    dataset_id : str
        Dataset the field belongs to, used to name the offender.
    field : str
        Field name, used the same way.
    declared : Any
        Raw value; ``None`` means absent.

    Returns
    -------
    str or None
        The trimmed text, or ``None`` when absent.

    Raises
    ------
    ValueError
        The field is present but empty.
    """
    if declared is None:
        return None
    text = str(declared).strip()
    if not text:
        raise ValueError(f"{dataset_id}: '{field}' is present but empty")
    return text


def _parse_upstream(dataset_id: str, declared: Any, include: Any) -> Upstream:
    """Parse an ``upstream`` reference and the file selection beside it.

    Parameters
    ----------
    dataset_id : str
        Dataset the upstream belongs to, used to name it on failure.
    declared : Any
        Value of the ``upstream`` key.
    include : Any
        Value of the ``include`` key, or ``None`` for the default selection.

    Returns
    -------
    Upstream
        The parsed upstream.

    Raises
    ------
    ValueError
        The reference is not ``<repo>@<40-character commit sha>``.
    """
    repo, _, revision = str(declared).partition("@")
    if not repo or not _COMMIT.fullmatch(revision):
        raise ValueError(
            f"{dataset_id}: upstream must be '<repo>@<40-character commit sha>', got {declared!r}"
        )

    declared_include = include
    patterns = DEFAULT_INCLUDE if declared_include is None else tuple(declared_include)
    return Upstream(repo, revision, patterns)


def _parse_labels(dataset_id: str, declared: Any) -> Labels | None:
    """Parse a pinned ``<repo>@<sha>#<path>`` class-vocabulary reference.

    Parameters
    ----------
    dataset_id : str
        Dataset the reference belongs to, used to name it on failure.
    declared : Any
        Value of the ``labels`` key, or ``None``.

    Returns
    -------
    Labels or None
        The parsed reference.

    Raises
    ------
    ValueError
        The reference is not ``<repo>@<40-character commit sha>#<path>``.
    """
    if declared is None:
        return None
    reference = str(declared)
    repo, _, rest = reference.partition("@")
    revision, separator, path = rest.partition("#")
    if not repo or not separator or not path or not _COMMIT.fullmatch(revision):
        raise ValueError(
            f"{dataset_id}: labels must be '<repo>@<40-character commit sha>#<path>', "
            f"got {reference!r}"
        )
    return Labels(repo, revision, path)
