"""The class vocabulary a mask annotation's colours index into.

A mask is an image whose pixel colours *are* the annotation, so the mapping from a colour to a class
is not derivable from the mask: it is published upstream, next to the model that produced it, and
pinned in `sources.yaml`. This module reads that artifact and answers the two questions the
publisher asks of it — which classes exist, and which of them one mask contains.

The palette is the only authority. Nothing here derives a colour from a figure, and a mask that
paints with a colour the palette does not define is refused rather than rounded to the nearest
class.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

COLOUR_FORMAT = "#{:02x}{:02x}{:02x}"
"""How a palette colour is stored: lower-case hexadecimal, as `docs/data-layout.md` says."""


class PaletteError(RuntimeError):
    """The pinned class vocabulary is unreadable or does not describe a usable palette."""


class UnknownMaskColour(ValueError):
    """A mask holds a colour, or more colours, than the pinned palette defines."""


@dataclass(frozen=True)
class Palette:
    """A pinned class vocabulary: the ids, names, and colours of a mask annotation.

    Attributes
    ----------
    classes : tuple of tuple
        `(id, name, colour)` per class, in the order the upstream published them, with the colour as
        `#rrggbb`. This is the index's `classes` table verbatim, and the only copy of the palette: a
        decoded mask reports a pixel as `(r, g, b)`, which :meth:`id_for_colour` renders back into
        the same string to compare.
    """

    classes: tuple[tuple[int, str, str], ...]

    @property
    def background_id(self) -> int | None:
        """Return the class this vocabulary names as background, where it names one.

        Returns
        -------
        int or None
            The id of the class whose name is `background`, case-insensitively, or `None` when the
            vocabulary has no such class.
        """
        for class_id, name, _ in self.classes:
            if name.strip().lower() == "background":
                return class_id
        return None

    def id_for_colour(self, colour: tuple[int, int, int]) -> int | None:
        """Return the class one pixel colour paints, where the palette defines it.

        Parameters
        ----------
        colour : tuple of int
            One decoded `(r, g, b)` pixel value.

        Returns
        -------
        int or None
            The class id, or `None` when no class is painted with that colour.
        """
        painted = COLOUR_FORMAT.format(*colour)
        for class_id, _, value in self.classes:
            if value == painted:
                return class_id
        return None

    def class_ids_in(self, image: Image.Image) -> tuple[int, ...]:
        """Return the palette classes a mask contains, in class-id order.

        Parameters
        ----------
        image : PIL.Image.Image
            The decoded mask.

        Returns
        -------
        tuple of int
            One id per palette colour that appears at least once, **excluding the background**:
            the background is the absence of an annotation, so recording it would put every row in
            the class facet and say nothing. A vocabulary that names no background excludes
            nothing.

        Raises
        ------
        UnknownMaskColour
            The mask holds a colour the palette does not define, or more distinct colours than the
            palette has. Nothing in a mask is expected to fall outside the palette: a colour that
            does means the mask and its legend disagree, and recording presence anyway would
            publish a wrong legend.

        Notes
        -----
        The counting stops at one colour more than the palette has, so a photographic mask is
        rejected without materialising a colour table the size of its pixel count.
        """
        counted = image.convert("RGB").getcolors(maxcolors=len(self.classes) + 1)
        if counted is None:
            raise UnknownMaskColour(
                f"the mask has more colours than the {len(self.classes)} the palette defines"
            )
        background = self.background_id
        found: list[int] = []
        for _, colour in counted:
            class_id = self.id_for_colour(_rgb(colour))
            if class_id is None:
                raise UnknownMaskColour(
                    f"the mask paints {colour!r}, which the palette does not define"
                )
            if class_id != background:
                found.append(class_id)
        return tuple(sorted(found))


def _rgb(colour: Any) -> tuple[int, int, int]:
    """Return one decoded pixel as an `(r, g, b)` triple.

    Parameters
    ----------
    colour : Any
        One entry of what `PIL.Image.getcolors` returned. The mask has been converted to RGB, so it
        is a three-tuple; the annotation is loose only because Pillow types the union of all modes.

    Returns
    -------
    tuple of int
        The three components.
    """
    red, green, blue = (int(component) for component in colour)
    return (red, green, blue)


def load_palette(path: Path) -> Palette:
    """Read a pinned class vocabulary.

    Parameters
    ----------
    path : pathlib.Path
        The cached `class.json`.

    Returns
    -------
    Palette
        The classes, in the order the artifact lists them.

    Raises
    ------
    PaletteError
        The file is absent, is not JSON, is empty, or does not hold `id`/`name`/`color` entries with
        three colour components and unique ids. Every refusal names the path; none guesses a
        palette.
    """
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PaletteError(f"{path}: {error}") from error
    if not isinstance(document, list) or not document:
        raise PaletteError(f"{path}: expected a non-empty JSON list of id/name/color entries")

    classes: list[tuple[int, str, str]] = []
    for entry in document:
        try:
            class_id = int(entry["id"])
            name = str(entry["name"])
            red, green, blue = (int(component) for component in entry["color"])
        except (KeyError, TypeError, ValueError) as error:
            raise PaletteError(f"{path}: unexpected class entry {entry!r}: {error}") from error
        classes.append((class_id, name, COLOUR_FORMAT.format(red, green, blue)))

    if len({class_id for class_id, _, _ in classes}) != len(classes):
        raise PaletteError(f"{path}: class ids are not unique")
    return Palette(classes=tuple(classes))
