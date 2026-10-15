"""Adapter registry: one reader per upstream parquet schema.

An adapter name describes a schema, not a dataset: gwhs and gwhs-temp share one. A name the package
does not implement is a hard error naming it, never a silent skip, because a dataset whose rows
cannot be read must not publish an empty index as if it succeeded.

A reader is built through :func:`get_adapter`, which is also where the one dependency a schema may
have on provenance is met: a mask annotation's classes come from the pinned vocabulary, so the
`gwfss` reader cannot exist without it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Protocol

from biohack_datasets.adapters.gwfss import GwfssAdapter
from biohack_datasets.adapters.gwhd import GwhdAdapter
from biohack_datasets.adapters.gwhs import GwhsAdapter
from biohack_datasets.labels import Palette
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


class MissingVocabulary(LookupError):
    """A mask schema's reader needs the dataset's pinned vocabulary, and none was supplied."""


def _gwfss(palette: Palette | None) -> Adapter:
    """Build the mask reader, which cannot exist without the dataset's pinned vocabulary.

    Parameters
    ----------
    palette : Palette or None
        The dataset's pinned vocabulary, where its source names one.

    Returns
    -------
    Adapter
        The reader.

    Raises
    ------
    MissingVocabulary
        No palette was supplied, so the mask's classes could not be named and guessing one would
        invent a legend.
    """
    if palette is None:
        raise MissingVocabulary(
            "adapter 'gwfss' reads a mask annotation and needs the dataset's pinned class"
            " vocabulary; its source in sources.yaml names no 'labels' URL"
        )
    return GwfssAdapter(palette)


_FACTORIES: dict[str, Callable[[Palette | None], Adapter]] = {
    "gwhd": lambda _: GwhdAdapter(),
    "gwhs": lambda _: GwhsAdapter(),
    "gwfss": _gwfss,
}
"""How each name is built: the box and RLE schemas ignore the palette."""


def check_adapter(name: str) -> None:
    """Refuse a name no reader is registered under, without building one.

    Parameters
    ----------
    name : str
        Adapter name from the provenance record.

    Returns
    -------
    None

    Raises
    ------
    AdapterNotImplemented
        No reader is registered under that name.
    """
    if name not in _FACTORIES:
        known = ", ".join(sorted(_FACTORIES))
        raise AdapterNotImplemented(f"adapter {name!r} is not implemented; implemented: {known}")


def get_adapter(name: str, palette: Palette | None = None) -> Adapter:
    """Build the reader registered under an adapter name.

    Parameters
    ----------
    name : str
        Adapter name from the provenance record.
    palette : Palette or None, optional
        The dataset's pinned class vocabulary, where it has one.

    Returns
    -------
    Adapter
        The reader.

    Raises
    ------
    AdapterNotImplemented
        No reader is registered under that name. The message names it and lists what is.
    MissingVocabulary
        The reader is for a mask schema and no palette was supplied.
    """
    check_adapter(name)
    return _FACTORIES[name](palette)
