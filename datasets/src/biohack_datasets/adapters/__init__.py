"""Adapter registry: one reader per upstream parquet schema.

An adapter name describes a schema, not a dataset: gwhs and gwhs-temp share one. A name the
package does not implement is a hard error naming it, never a silent skip, because a
dataset whose rows cannot be read must not publish an empty index as if it succeeded.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Protocol

from biohack_datasets.adapters.gwhs import GwhsAdapter
from biohack_datasets.model import Row


class Adapter(Protocol):
    """Reads one upstream schema into neutral rows."""

    mask_kind: str | None
    """Kind of mask object the schema ships, or None when it ships none."""

    def iter_rows(self, files: Sequence[Path]) -> Iterator[Row]:
        """Yield one neutral row per absolute row number, in order.

        Parameters
        ----------
        files : sequence of pathlib.Path
            Parquet files of one dataset, sorted by repository-relative path.

        Yields
        ------
        Row
            The next image and its annotation.
        """
        ...


class AdapterNotImplemented(LookupError):
    """The provenance record names an adapter this package does not implement."""


_ADAPTERS: dict[str, Adapter] = {"gwhs": GwhsAdapter()}


def get_adapter(name: str) -> Adapter:
    """Return the reader registered under an adapter name.

    Parameters
    ----------
    name : str
        Adapter name from the provenance record.

    Returns
    -------
    Adapter
        The registered reader.

    Raises
    ------
    AdapterNotImplemented
        No reader is registered under that name. The message names it and lists what is.
    """
    try:
        return _ADAPTERS[name]
    except KeyError:
        known = ", ".join(sorted(_ADAPTERS))
        raise AdapterNotImplemented(
            f"adapter {name!r} is not implemented; implemented: {known}"
        ) from None
