"""Turn the notebook page into an `.ipynb`, so one markdown page is the notebook's source.

The page a reader scrolls and the notebook they download have to be the same document written once:
two hand-kept copies drift, and the one nobody builds is the one that rots. So `examples/notebook.md`
is the source and `examples/quickstart.ipynb` beside it is generated from it — before the build, so
the build publishes the fresh one, and committed, because Colab can only open a notebook that sits at
a GitHub path. `git diff --exit-code` in the workflow is what notices a `.ipynb` that was not
regenerated and committed with the page that changed.

The page therefore has to be convertible, and the converter refuses what it cannot carry rather than
publishing a notebook with a hole in it:

- `python` and `bash` fences only. A `bash` fence becomes a code cell whose lines carry `!`, which is
  how a notebook runs a shell command; an `export` line is refused, because it would set a variable
  for one cell and no other.
- No language tabs (`=== "Python"`). A notebook has one kernel, so a page with tabs is a page with
  three versions of itself.
- No admonitions. Jupyter renders `!!! note` as literal text, so a note is a bold lead-in.
- Relative images are rewritten to the site's absolute URL, because a notebook opened in Colab has no
  file beside it to resolve them against.
- Anything between `<!-- notebook: skip -->` and `<!-- /notebook: skip -->` stays on the page: that is
  where the download buttons live, and a button is not a cell.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
"""The repository root, two directories above `docs/scripts/`."""

SOURCE = REPO / "docs/content/examples/notebook.md"
"""The page that is also the notebook, and the only place its content is written."""

TARGET = REPO / "docs/content/examples/quickstart.ipynb"
"""The generated notebook, beside its source: the build publishes it from there."""

SITE = "https://docs.global-wheat.com/examples/"
"""What a relative image is rewritten to — the page's own directory on the site."""

SKIP = re.compile(r"<!--\s*/?\s*notebook: skip\s*-->")
"""The markers around content that belongs to the page and not to the notebook."""

FENCE = re.compile(r"^```(?P<lang>[A-Za-z0-9_+-]*)\s*$")
"""A fence's opening or closing line; the language is empty when it carries none."""

IMAGE = re.compile(r"(!\[[^\]]*\]\()(?P<path>[^)\s]+)(\))")
"""A markdown image, whose path may be relative to the page."""


class Unconvertible(Exception):
    """The page holds markdown a notebook cannot carry, and the build must stop."""


def strip_skipped(text: str) -> str:
    """Remove every block that is the page's own.

    Parameters
    ----------
    text : str
        The page's markdown.

    Returns
    -------
    str
        The page without the marked blocks, markers included.
    """
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if SKIP.fullmatch(line.strip()):
            skipping = not line.strip().startswith("<!-- /")
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


def to_code(language: str, body: list[str], where: int) -> list[str]:
    """Turn one fence's body into cell source.

    Parameters
    ----------
    language : str
        ``python`` or ``bash``; anything else has already been refused.
    body : list of str
        The fence's lines, as written on the page.
    where : int
        The line the fence opened on, for the refusal message.

    Returns
    -------
    list of str
        The cell's source lines, each ending in a newline except the last.

    Raises
    ------
    Unconvertible
        A `bash` line would set an environment variable for one cell only.
    """
    if language == "python":
        return body
    for line in body:
        if line.startswith("export "):
            raise Unconvertible(
                f"notebook.md:{where}: `export` in a shell cell would reach no later cell — "
                "write it as `os.environ[...]` in a python fence"
            )
    return [line if line.startswith(("!", "#")) or not line.strip() else f"!{line}" for line in body]


def absolutise(line: str) -> str:
    """Point a relative image at the site, which is where the notebook's reader can see it.

    Parameters
    ----------
    line : str
        One line of the page.

    Returns
    -------
    str
        The line with every relative image path made absolute; an absolute one is left alone.
    """

    def replace(match: re.Match[str]) -> str:
        path = match["path"]
        if "://" in path or path.startswith(("/", "data:")):
            return match[0]
        return f"{match[1]}{SITE}{Path(path).name}{match[3]}"

    return IMAGE.sub(replace, line)


def cells(markdown: str) -> list[dict[str, object]]:
    """Split the page into notebook cells.

    Parameters
    ----------
    markdown : str
        The page's text, with skipped blocks already removed.

    Returns
    -------
    list of dict
        nbformat cells, in reading order, with stable ids.

    Raises
    ------
    Unconvertible
        The page holds a construct a notebook cannot carry.
    """
    made: list[dict[str, object]] = []
    prose: list[str] = []

    def flush() -> None:
        while prose and not prose[0].strip():
            prose.pop(0)
        while prose and not prose[-1].strip():
            prose.pop()
        tightened: list[str] = []
        for line in prose:
            if line.strip() or not tightened or tightened[-1].strip():
                tightened.append(line)
        prose[:] = tightened
        if prose:
            made.append(
                {
                    "cell_type": "markdown",
                    "id": f"cell-{len(made)}",
                    "metadata": {},
                    "source": [line + "\n" for line in prose[:-1]] + [prose[-1]],
                }
            )
            prose.clear()

    lines = markdown.splitlines()
    at = 0
    while at < len(lines):
        line = lines[at]
        if line.startswith('=== "') or line.startswith("=== '"):
            raise Unconvertible(
                f"notebook.md:{at + 1}: a language tab has no notebook equivalent — "
                "this page is one language"
            )
        if line.startswith("!!! "):
            raise Unconvertible(
                f"notebook.md:{at + 1}: an admonition renders as literal text in Jupyter — "
                "use a bold lead-in"
            )
        fence = FENCE.match(line)
        if not fence:
            prose.append(absolutise(line))
            at += 1
            continue
        language = fence["lang"]
        if language not in {"python", "bash"}:
            raise Unconvertible(
                f"notebook.md:{at + 1}: fence language {language or '(none)'!r} is not python or bash"
            )
        close = at + 1
        while close < len(lines) and lines[close].strip() != "```":
            close += 1
        if close == len(lines):
            raise Unconvertible(f"notebook.md:{at + 1}: this fence is never closed")
        flush()
        body = to_code(language, lines[at + 1 : close], at + 1)
        made.append(
            {
                "cell_type": "code",
                "execution_count": None,
                "id": f"cell-{len(made)}",
                "metadata": {},
                "outputs": [],
                "source": [line + "\n" for line in body[:-1]] + ([body[-1]] if body else []),
            }
        )
        at = close + 1
    flush()
    return made


def notebook(cells_: list[dict[str, object]]) -> dict[str, object]:
    """Wrap cells in the notebook envelope, in the shape Jupyter and Colab both read.

    Parameters
    ----------
    cells_ : list of dict
        The cells, in reading order.

    Returns
    -------
    dict
        An nbformat 4.5 notebook with no outputs.
    """
    return {
        "cells": cells_,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def check(nb: dict[str, object]) -> None:
    """Refuse a notebook that Jupyter would not open.

    Parameters
    ----------
    nb : dict
        What :func:`notebook` built.

    Raises
    ------
    Unconvertible
        A cell is missing the fields nbformat requires, or there are no cells at all.
    """
    problems: list[str] = []
    if nb.get("nbformat") != 4 or not isinstance(nb.get("nbformat_minor"), int):
        problems.append("no nbformat version")
    if not nb.get("cells"):
        problems.append("no cells")
    for at, cell in enumerate(nb.get("cells") or []):
        kind = cell.get("cell_type")
        if kind not in {"markdown", "code"}:
            problems.append(f"cell {at}: cell_type {kind!r}")
        elif not isinstance(cell.get("source"), list):
            problems.append(f"cell {at}: source is not a list of lines")
        elif kind == "code" and "outputs" not in cell:
            problems.append(f"cell {at}: code cell without outputs")
    if problems:
        raise Unconvertible("; ".join(problems))


def write() -> int:
    """Generate the notebook and report how many cells it holds.

    Returns
    -------
    int
        The number of cells written.

    Raises
    ------
    FileNotFoundError
        The page this notebook is made from is missing.
    Unconvertible
        The page holds markdown a notebook cannot carry.
    """
    if not SOURCE.is_file():
        raise FileNotFoundError(f"{SOURCE.relative_to(REPO)} is not there")
    nb = notebook(cells(strip_skipped(SOURCE.read_text())))
    check(nb)
    TARGET.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
    return len(nb["cells"])  # type: ignore[arg-type]


def main() -> int:
    """Run the conversion, and say why not when it cannot.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the page cannot become a notebook.
    """
    try:
        count = write()
    except (FileNotFoundError, Unconvertible) as problem:
        print(f"make_notebook: {problem}")
        return 1
    print(f"wrote {TARGET.relative_to(REPO)} ({count} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
