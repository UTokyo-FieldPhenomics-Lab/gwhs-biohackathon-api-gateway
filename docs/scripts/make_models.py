#!/usr/bin/env python3
"""Render a model page's parameter table from a recorded ``GET /models``.

The table is mechanical — it is the model's own declaration, laid out — so it is generated rather
than transcribed. ``docs/scripts/fixtures/model-directory.json`` is a real answer from the live API,
recorded once; this script rewrites the block between two markers in the page, and CI re-runs it and
fails on a diff, exactly as it does for the quickstart notebook. That is what keeps the page from
saying one thing while the container declares another.

Generation instead of execution at build time is deliberate: the API answers ``GET /models`` only
with a key, and a docs build that held one would make a participant-facing site depend on a secret
and on a host being up. The fixture is the record, the script is the transcription, and the stale
check is the refresh mechanism.

    python3 docs/scripts/make_models.py
    python3 docs/scripts/make_models.py --model sam3.1-meta --check   # for a dry run

To re-record the fixture, point a gateway at the running container and ask once:

    curl -s -H "X-API-Key: $KEY" "$API/models" -o docs/scripts/fixtures/model-directory.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
"""The repository root, two directories above `docs/scripts/`."""

FIXTURE = REPO / "docs/scripts/fixtures/model-directory.json"
"""A recorded `GET /models`, refreshed by hand when a container's declaration changes."""

START = "<!-- parameters:start -->"
END = "<!-- parameters:end -->"
"""The markers the generated table lives between. Everything outside them is written by a person."""


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
            return f"`{_number(low)}` – `{_number(high)}`"
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
        Markdown: a table, then one line saying where it came from.
    """
    parameters = entry.get("parameters") or []
    if not parameters:
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


def rewrite(page: Path, table: str) -> bool:
    """Put a rendered table between the page's markers.

    Parameters
    ----------
    page : Path
        The page carrying the markers.
    table : str
        What :func:`render` produced.

    Returns
    -------
    bool
        Whether the file changed.

    Raises
    ------
    SystemExit
        When the page has no markers, which would otherwise silently drop the table.
    """
    text = page.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{page} has no {START} … {END} block to write into")
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    updated = f"{head}{START}\n{table}\n{END}{tail}"
    if updated == text:
        return False
    page.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    """Render the table into its page.

    Returns
    -------
    int
        `0` once the page is current. CI runs this and then `git diff --exit-code`, the same stale
        check the quickstart notebook gets, so a container whose declaration moved and a page that
        did not are a failing build rather than a quiet lie.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="sam3.1-meta")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--page", type=Path, default=None)
    args = parser.parse_args()

    page = args.page or REPO / "docs" / "content" / "models" / f"{args.model}.md"
    directory = json.loads(args.fixture.read_text(encoding="utf-8"))
    entry = next((model for model in directory["models"] if model["id"] == args.model), None)
    if entry is None:
        raise SystemExit(f"{args.model} is not in {args.fixture}; re-record it")

    changed = rewrite(page, render(entry))
    print(f"{page.relative_to(REPO)}: {'rewritten' if changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
