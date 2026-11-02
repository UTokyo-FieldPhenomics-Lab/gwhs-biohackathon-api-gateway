#!/usr/bin/env python3
"""Render a model's parameter table from a recorded ``GET /models``.

The table is mechanical — it is the model's own declaration, laid out — so it is generated rather
than transcribed. ``docs/scripts/fixtures/model-directory.json`` is a real answer from the live API,
recorded once; this script rewrites the block between two markers in the page, and CI re-runs it and
fails on a model-page diff. Unlike the automatically synchronized notebook, these tables must be
reviewed and committed with their recorded declaration.

A page may cover one id or a family. A family declares its ids in an HTML comment —
``<!-- models: id-one id-two -->`` — and carries one ``parameters:<id>`` block per id, so the shared
prose is written once and the declarations cannot overwrite one another. A page without the comment
is a single-model page named after its id.

Generation instead of execution at build time is deliberate: the API answers ``GET /models`` only
with a key, and a docs build that held one would make a participant-facing site depend on a secret
and on a host being up. The fixture is the record, the script is the transcription, and the stale
check is the refresh mechanism.

    python3 docs/scripts/make_models.py
    python3 docs/scripts/make_models.py --model sam3.1-meta   # one id, for a quicker check

To re-record the fixture, point a gateway at the running container and ask once:

    curl -s -H "X-API-Key: $KEY" "$API/models" -o docs/scripts/fixtures/model-directory.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
"""The repository root, two directories above `docs/scripts/`."""

FIXTURE = REPO / "docs/scripts/fixtures/model-directory.json"
"""A recorded `GET /models`, refreshed by hand when a container's declaration changes."""

SITE = REPO / "zensical.toml"
"""The site's own configuration, whose `nav` is the only list of pages a reader can reach."""

START = "<!-- parameters:start -->"
END = "<!-- parameters:end -->"
"""The markers a single-model page uses for its table. Everything outside them is written by a person."""

MODELS = re.compile(r"<!--\s*models:\s*([^>]*?)\s*-->")
"""The comment a page uses to declare the ids it covers.

A page per **model family** is the right shape when the ids differ only in a checkpoint: the
contract, the output and the analysis are shared, and copying them once per id produces two pages
that drift. A page that declares several ids carries one generated block per id, named
``parameters:<id>``; a page without the comment is a single-model page named after its id, which is
what the three task-model pages are.
"""


def _number(value: float) -> str:
    """Format a declared number the way a reader writes it: `1008`, not `1008.0`."""
    return f"{value:g}"


def _default(parameter: dict[str, Any]) -> str:
    """What the model uses when the client sends nothing."""
    value = parameter.get("default")
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, (int, float)):
        return f"`{_number(float(value))}`"
    return f"`{value}`"


def _accepts(parameter: dict[str, Any]) -> str:
    """What the declaration says this parameter takes, in one cell."""
    kind = parameter.get("type", "text")
    values = parameter.get("values")
    if kind == "boxes":
        return "`[[x, y, w, h, label], …]`, in the image's pixels"
    if kind == "points":
        return "`[[x, y, label], …]`, in the image's pixels"
    if kind == "enum":
        return ", ".join(f"`{value}`" for value in parameter.get("enum") or [])
    if kind == "boolean":
        return "`true` or `false`"
    if values:
        listed = ", ".join(f"`{_number(float(value))}`" for value in values)
        if parameter.get("above_maximum") == "clamp":
            return f"{listed}; a larger value runs at `{_number(max(values))}`"
        return listed
    if kind == "number":
        low, high = parameter.get("minimum"), parameter.get("maximum")
        if low is not None and high is not None:
            span = f"`{_number(low)}` – `{_number(high)}`"
            # A ceiling the container clamps is a different offer from one the gateway refuses:
            # saying only the range would hide the sentence the answer will carry.
            if parameter.get("above_maximum") == "clamp":
                span += f"; a larger value runs at `{_number(high)}`"
            return span
        if low is not None:
            return f"at least `{_number(low)}`"
        if high is not None:
            return f"at most `{_number(high)}`"
        return "a number"
    if parameter.get("max_length"):
        return f"text, at most {parameter['max_length']} characters"
    return "text"


def render(entry: dict[str, Any]) -> str:
    """Render one model's parameter table.

    Parameters
    ----------
    entry : dict
        One model from a `GET /models` answer.

    Returns
    -------
    str
        Markdown: one table of the model's parameters, or a sentence saying it has none.
    """
    parameters = entry.get("parameters") or []
    if not parameters:
        if entry.get("declared"):
            # A model that declares nothing is a different fact from one that has not declared yet,
            # and the gateway publishes which it is. Printing "no backend has declared this model"
            # for a model that answered its declaration would be a page saying the opposite of the
            # truth, which is what this table exists to prevent.
            return (
                "This model declares **no parameters**: an image is the whole request, and a query "
                "string is refused rather than ignored."
            )
        return (
            f"No backend has declared this model yet, so `GET /models/{entry['id']}` lists no "
            "parameters. Nothing about the model's surface is guessed here."
        )
    rows = [
        "| parameter | default | accepts | what it does |",
        "|---|---|---|---|",
    ]
    for parameter in parameters:
        rows.append(
            f"| `{parameter['name']}` | {_default(parameter)} | {_accepts(parameter)} | "
            f"{parameter.get('description', '')} |"
        )
    return "\n".join(rows)


def navigation() -> set[str]:
    """Every page path the site's navigation names.

    Returns
    -------
    set of str
        Paths as `zensical.toml` writes them, e.g. ``models/sam3.1-meta.md``.

    Notes
    -----
    The Models section is enumerated there rather than collected from the directory, so a page can
    exist, build, and answer its URL while no reader can reach it — which is exactly what happened
    when the second model page landed. This is the check that would have caught it.
    """

    def walk(node: Any) -> set[str]:
        if isinstance(node, str):
            return {node}
        if isinstance(node, dict):
            found: set[str] = set()
            for value in node.values():
                found |= walk(value)
            return found
        if isinstance(node, list):
            found = set()
            for item in node:
                found |= walk(item)
            return found
        return set()

    document = tomllib.loads(SITE.read_text(encoding="utf-8"))
    # `nav` lives under `[project]`; everything the site configures does.
    return walk(document["project"]["nav"])


def rewrite(page: Path, table: str, start: str = START, end: str = END) -> bool:
    """Put a rendered table between one pair of the page's markers.

    Parameters
    ----------
    page : Path
        The page carrying the markers.
    table : str
        What :func:`render` produced.
    start, end : str
        The pair to write between. A single-model page uses the bare pair; a page covering several
        ids names the id in each pair, so its blocks cannot overwrite one another.

    Returns
    -------
    bool
        Whether the file changed.

    Raises
    ------
    SystemExit
        When the page has no such pair, which would otherwise silently drop the table.
    """
    text = page.read_text(encoding="utf-8")
    if start not in text or end not in text:
        raise SystemExit(f"{page} has no {start} … {end} block to write into")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    updated = f"{head}{start}\n{table}\n{end}{tail}"
    if updated == text:
        return False
    page.write_text(updated, encoding="utf-8")
    return True


def model_pages(models: set[str]) -> dict[str, Path]:
    """Find the page that covers each model id.

    Parameters
    ----------
    models : set of str
        The ids the recorded directory lists.

    Returns
    -------
    dict of str to Path
        One page per id. A page declares a family with ``<!-- models: … -->``; a page without the
        comment covers the id its filename names.

    Raises
    ------
    SystemExit
        When a declared id is not in the directory, when two pages claim one id, or when an id has
        no page. Each is a page that would build and answer without saying what the model is.
    """
    pages: dict[str, Path] = {}
    for page in sorted((REPO / "docs" / "content" / "models").glob("*.md")):
        text = page.read_text(encoding="utf-8")
        declared = MODELS.search(text)
        if declared:
            claimed = declared.group(1).split()
        elif page.stem in models:
            claimed = [page.stem]
        else:
            continue
        if not claimed:
            raise SystemExit(f"{page} declares no model id")
        unknown = [model for model in claimed if model not in models]
        if unknown:
            raise SystemExit(
                f"{page} declares {unknown}, which the recorded directory does not list"
            )
        for model in claimed:
            if model in pages:
                raise SystemExit(f"{model} is claimed by both {pages[model]} and {page}")
            pages[model] = page
    missing = sorted(models - set(pages))
    if missing:
        raise SystemExit(f"no page covers {missing}; every model in the directory has a page")
    return pages


def markers(page: Path, model: str) -> tuple[str, str]:
    """Return the marker pair one model's table lives between.

    Parameters
    ----------
    page : Path
        The page, which may cover one id or a family.
    model : str
        The id whose block is wanted.

    Returns
    -------
    tuple of str
        ``(start, end)``. A family page uses ``parameters:<id>:start`` and its end; a single-model
        page keeps the bare pair.

    Raises
    ------
    SystemExit
        When a family page has no block for one of the ids it declares. Falling back to the bare
        pair would make the second id's table overwrite the first.
    """
    text = page.read_text(encoding="utf-8")
    start = f"<!-- parameters:{model}:start -->"
    end = f"<!-- parameters:{model}:end -->"
    if start in text:
        return start, end
    declared = MODELS.search(text)
    if declared and len(declared.group(1).split()) > 1:
        raise SystemExit(f"{page} covers several ids and needs a {start} … {end} block")
    return START, END


def main() -> int:
    """Render the table into its page.

    Returns
    -------
    int
        `0` once the page is current. CI runs this and then checks the model-page diff, refusing
        an uncommitted table change rather than automatically accepting a changed declaration.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", default=None, help="one model id; by default, every model the directory lists"
    )
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--page", type=Path, default=None)
    args = parser.parse_args()

    directory = json.loads(args.fixture.read_text(encoding="utf-8"))
    pages = model_pages({model["id"] for model in directory["models"]})
    if args.model:
        entries = [model for model in directory["models"] if model["id"] == args.model]
        if not entries:
            raise SystemExit(f"{args.model} is not in {args.fixture}; re-record it")
    else:
        # Every id the directory lists is a model the API answers, so every one of them has a page
        # (alone or in a family). Iterating is what makes CI notice a deployed model whose page was
        # never written: the run fails on the missing page rather than printing "already current"
        # for the one page it knows.
        entries = directory["models"]

    reachable = navigation()
    for entry in entries:
        model = entry["id"]
        page = args.page if (args.page and args.model) else pages[model]
        if not page.is_file():
            raise SystemExit(f"{page} does not exist; every model in the directory has a page")
        if not args.page and f"models/{page.name}" not in reachable:
            raise SystemExit(
                f"models/{page.name} is not in zensical.toml's nav: the page builds and "
                "answers its URL, and no reader can reach it"
            )
        start, end = markers(page, model)
        changed = rewrite(page, render(entry), start, end)
        print(
            f"{page.relative_to(REPO)} [{model}]: "
            f"{'rewritten' if changed else 'already current'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
