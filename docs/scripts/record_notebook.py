"""Record what the quickstart's cells print, one real run at a time, and write it onto the page.

The page shows a result under every cell that produces one, because a participant reading it wants
to know what comes back before they type it. A notebook must not: it is the thing they run, and a
cell that already holds our numbers is a cell that hides whether theirs were right. So the outputs
live on the page, in `<!-- output -->` regions that `make_notebook.py` drops, and they are recorded
— never written by hand, because a number typed by hand is a number nobody has re-checked.

Recording is one command, and it needs a key and the network::

    set -a; . ./.env; set +a
    uv run --project docs --with nbconvert --with ipykernel --with requests --with numpy \
        --with pillow python docs/scripts/record_notebook.py

The key comes from `GLOBAL_WHEAT_API_KEY`, or from `TEST_API_TOKEN` in the repository's `.env` — the
token the client suites use. Every cell in the page runs, in the order the page has them, in a
temporary directory: the page's own `curl` is what puts `global_wheat.py` there, so a snippet that
would not run for a participant fails here. `nbconvert` executes the notebook `make_notebook.py`
just generated, so what is recorded is what a reader's kernel would show, and a cell that raises
stops the run rather than shipping half a transcript.

What is kept: `stdout` and each cell's result, as text, as a picture, or as the HTML Jupyter would
render. What is not: `stderr`, because a library's warning on our machine is noise on the page, and
a participant's kernel would not print it either. A cell with nothing on stdout and no result
simply gets no region — the page's `curl` is quiet for that reason.

Images are written beside the page as `quickstart-step<N>.png`, numbered by the page's own step
heading; the recorder deletes any it did not just write, so a picture that is no longer produced
does not linger in the tree.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import make_notebook

EXAMPLES = make_notebook.SOURCE.parent
"""The directory the page lives in, and where a recorded picture is written beside it."""

SECTION = re.compile(r"^##\s+(?P<step>\d+)\.")
"""A step heading: the number an image is named after comes from here."""

OPEN = "<!-- output -->"
"""Where a recorded region starts. `make_notebook.OUTPUT` is what reads both ends."""

CLOSE = "<!-- /output -->"
"""Where a recorded region ends."""

KEY_ENVS = ("GLOBAL_WHEAT_API_KEY", "TEST_API_TOKEN")
"""Where the key is looked for, in order: the client's own variable, then the suites' `.env`."""


class Unrecordable(Exception):
    """The run cannot produce a page whose outputs are true, and it must stop here."""


@dataclass(frozen=True)
class Block:
    """One rendered result: a cell can produce several, in order.

    Parameters
    ----------
    kind : str
        ``text``, ``html`` or ``image``.
    body : str
        The text itself, the HTML itself, or a base64 PNG.
    """

    kind: str
    body: str


def key() -> str:
    """Find a participant key in the environment.

    Returns
    -------
    str
        The key, verbatim.

    Raises
    ------
    Unrecordable
        Neither variable holds a non-empty value.
    """
    for name in KEY_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise Unrecordable(
        f"no key: set {' or '.join(KEY_ENVS)} — the cells call the live API, so recording needs one"
    )


def execute(workdir: Path, token: str) -> dict[str, object]:
    """Run the notebook the page converts to, and hand back what it produced.

    Parameters
    ----------
    workdir : pathlib.Path
        An empty directory to run in: the page's `curl` writes the client here, and nothing it
        downloads or writes lands in the repository.
    token : str
        The key, exported to the kernel so the page's `getpass` cell is a no-op.

    Returns
    -------
    dict
        The executed notebook.

    Raises
    ------
    Unrecordable
        The page cannot be converted, the run is not installed, or a cell failed.
    """
    try:
        nb = make_notebook.notebook(
            make_notebook.cells(make_notebook.strip_skipped(make_notebook.SOURCE.read_text()))
        )
    except make_notebook.Unconvertible as problem:
        raise Unrecordable(str(problem)) from problem
    path = workdir / "quickstart.ipynb"
    path.write_text(json.dumps(nb))
    environment = os.environ | {"GLOBAL_WHEAT_API_KEY": token, "MPLBACKEND": "Agg"}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            "--inplace",
            "--ExecutePreprocessor.timeout=600",
            path.name,
        ],
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode:
        raise Unrecordable(f"a cell failed, so nothing was recorded:\n{run.stderr.strip()[-2000:]}")
    return json.loads(path.read_text())


def collect(nb: dict[str, object]) -> list[list[Block]]:
    """Read every code cell's results, in cell order.

    Parameters
    ----------
    nb : dict
        The executed notebook.

    Returns
    -------
    list of list of Block
        One list per code cell, aligned with the page's fences.

    Raises
    ------
    Unrecordable
        A cell carries an error output, which nbconvert should already have stopped on.
    """
    return [read_cell(cell) for cell in nb["cells"] if cell["cell_type"] == "code"]  # type: ignore[union-attr,index]


def read_cell(cell: dict[str, object]) -> list[Block]:
    """Read one code cell's results, keeping stdout and results and dropping everything else.

    Parameters
    ----------
    cell : dict
        One code cell of the executed notebook.

    Returns
    -------
    list of Block
        The cell's results in the order it produced them, with adjacent stdout merged.

    Raises
    ------
    Unrecordable
        The cell carries an error.
    """
    found: list[Block] = []
    for output in cell.get("outputs") or []:  # type: ignore[union-attr]
        kind = output.get("output_type")
        if kind == "stream":
            if output.get("name") == "stdout":
                found = append_text(found, joined(output.get("text")))
        elif kind in {"execute_result", "display_data"}:
            data = output.get("data") or {}
            if "image/png" in data:
                found.append(Block("image", joined(data["image/png"])))
            elif "text/html" in data:
                found.append(Block("html", joined(data["text/html"])))
            elif "text/plain" in data:
                found = append_text(found, joined(data["text/plain"]))
        elif kind == "error":
            raise Unrecordable(f"a cell raised {output.get('ename')}: {output.get('evalue')}")
    return found


def joined(value: object) -> str:
    """Join an nbformat string-or-lines field.

    Parameters
    ----------
    value : object
        What the notebook holds: a list of lines, a string, or nothing.

    Returns
    -------
    str
        The field as one string.
    """
    if isinstance(value, list):
        return "".join(str(line) for line in value)
    return str(value or "")


def append_text(found: list[Block], text: str) -> list[Block]:
    """Add stdout to the block before it, so one cell's prints read as one transcript.

    Parameters
    ----------
    found : list of Block
        The blocks read so far for this cell.
    text : str
        More stdout from the same cell.

    Returns
    -------
    list of Block
        The same list, with the text merged into a trailing text block when there is one.
    """
    if found and found[-1].kind == "text":
        found[-1] = Block("text", found[-1].body + text)
        return found
    return [*found, Block("text", text)]


def text_lines(text: str) -> list[str]:
    """Trim a printed block to the lines worth showing.

    Trailing spaces are dropped because markdown treats two of them as a line break, and blank
    lines at both ends are dropped because the region's own blank lines already separate it from
    the prose.

    Parameters
    ----------
    text : str
        What a cell printed.

    Returns
    -------
    list of str
        The block's lines, with no blank line at either end.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def picture(block: Block, step: str, taken: int, written: list[Path]) -> list[str]:
    """Write one display result beside the page and return the markdown that shows it.

    Parameters
    ----------
    block : Block
        The image block, its body the base64 PNG Jupyter would have shown.
    step : str
        The page's step number, which the file is named after.
    taken : int
        How many pictures this cell has already written, so a second one gets a suffixed name.
    written : list of pathlib.Path
        Where every file written this run is collected, so stale ones can be removed afterwards.

    Returns
    -------
    list of str
        A markdown image, pointing at a file beside the page.

    Raises
    ------
    Unrecordable
        The picture is outside a numbered step, which is the only thing its name comes from.
    """
    if not step:
        raise Unrecordable("a cell displays a picture outside a numbered step, so it has no name")
    suffix = "" if taken == 1 else f"-{taken}"
    path = EXAMPLES / f"quickstart-step{step}{suffix}.png"
    path.write_bytes(base64.b64decode(block.body))
    written.append(path)
    return [f"![What step {step} returns]({path.name})"]


def html_block(block: Block) -> list[str]:
    """Wrap a rendered result in a block the stylesheet can treat as an output.

    The wrapper carries no `markdown` attribute on purpose: what Jupyter renders is already HTML,
    and running it through markdown again is how a table ends up as text.

    Parameters
    ----------
    block : Block
        The display block, its body the HTML Jupyter would have shown.

    Returns
    -------
    list of str
        The wrapper, a blank line, the HTML, and the closing tag.
    """
    return ['<div class="output">', "", *block.body.strip().splitlines(), "", "</div>"]


def region(blocks: list[Block], step: str, written: list[Path]) -> list[str]:
    """Turn one cell's results into the page's output region.

    Parameters
    ----------
    blocks : list of Block
        What the cell produced, in order.
    step : str
        The page's step number, used to name a picture.
    written : list of pathlib.Path
        Where files written this run are collected.

    Returns
    -------
    list of str
        The marked region, or nothing when the cell produced nothing to show.
    """
    shown: list[list[str]] = []
    pictures = 0
    for block in blocks:
        if block.kind == "image":
            pictures += 1
            shown.append(picture(block, step, pictures, written))
        elif block.kind == "html":
            shown.append(html_block(block))
        else:
            lines = text_lines(block.body)
            if lines:
                shown.append(["``` { .text .output }", *lines, "```"])
    if not shown:
        return []
    body: list[str] = []
    for part in shown:
        if body:
            body.append("")
        body.extend(part)
    return [OPEN, *body, CLOSE]


def splice(page: str, recorded: list[list[Block]]) -> tuple[str, list[Path]]:
    """Put this run's results onto the page, replacing whatever an earlier run left there.

    Parameters
    ----------
    page : str
        The page's markdown, output regions and all.
    recorded : list of list of Block
        One list per code cell, in the order the page's fences have them.

    Returns
    -------
    tuple of (str, list of pathlib.Path)
        The page with fresh regions, and the pictures this run wrote.
    """
    lines = page.splitlines()
    out: list[str] = []
    written: list[Path] = []
    cell = 0
    step = ""
    at = 0
    skipping = False
    while at < len(lines):
        line = lines[at]
        stripped = line.strip()
        if make_notebook.OUTPUT.fullmatch(stripped):
            skipping = not stripped.startswith("<!-- /")
            at += 1
            continue
        if skipping:
            at += 1
            continue
        heading = SECTION.match(line)
        if heading:
            step = heading["step"]
        fence = make_notebook.FENCE.match(line)
        if fence and fence["lang"] in {"python", "bash"}:
            close = at + 1
            while close < len(lines) and lines[close].strip() != "```":
                close += 1
            out.extend(lines[at : close + 1])
            at = close + 1
            blocks = recorded[cell] if cell < len(recorded) else []
            cell += 1
            recorded_here = region(blocks, step, written)
            if recorded_here:
                out.append("")
                out.extend(recorded_here)
            continue
        out.append(line)
        at += 1
    return tidy("\n".join(out)) + "\n", written


def tidy(page: str) -> str:
    """Collapse blank lines an earlier region left behind.

    Parameters
    ----------
    page : str
        The page's lines, joined.

    Returns
    -------
    str
        The same page with no run of two blank lines, so the diff shows the output and not the gaps.
    """
    out: list[str] = []
    for line in page.splitlines():
        if line or not out or out[-1]:
            out.append(line)
    return "\n".join(out)


def write() -> tuple[int, int]:
    """Record the page's outputs and write them back onto it.

    Returns
    -------
    tuple of (int, int)
        How many code cells produced something to show, and how many pictures were written.

    Raises
    ------
    Unrecordable
        The key is missing, a cell failed, or a picture was produced outside a numbered step.
    """
    token = key()
    page = make_notebook.SOURCE.read_text()
    with tempfile.TemporaryDirectory(prefix="quickstart-") as place:
        nb = execute(Path(place), token)
    recorded = collect(nb)
    spliced, written = splice(page, recorded)
    make_notebook.SOURCE.write_text(spliced)
    for stale in EXAMPLES.glob("quickstart-step*.png"):
        if stale not in written:
            stale.unlink()
    return sum(1 for blocks in recorded if blocks), len(written)


def main() -> int:
    """Run the recording, and say why not when it cannot.

    Returns
    -------
    int
        ``0`` on success, ``1`` when the run cannot be recorded.
    """
    try:
        cells, pictures = write()
    except (Unrecordable, OSError) as problem:
        print(f"record_notebook: {problem}")
        return 1
    print(f"recorded {cells} cells, {pictures} pictures onto {make_notebook.SOURCE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
