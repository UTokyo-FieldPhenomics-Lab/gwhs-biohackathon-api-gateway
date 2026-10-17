"""Put the three client files into the built site, after the site has been built.

`clients/` is where the sources live: the files the checks in this repository run against, and the
ones a review reads. The site is where a participant gets them, because a download that a page links
to has to be served from that page's origin — and because an ES module import needs a JavaScript
content type, which a raw GitHub URL does not carry.

The copy goes into the **build output**, not into `docs/content/`: one file, one place, and no copy
of a source file sitting in the source tree for somebody to edit by mistake. Zensical publishes what
it finds under `docs_dir`, so this runs between the build and the upload, and the download URLs the
Clients page shows are the paths that come out of it.

It therefore refuses to run before a build: a `site/` that does not hold the Clients page means the
build has not happened, and copying into it then would put the files somewhere the next build wipes.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
"""The repository root, two directories above `docs/scripts/`."""

SITE = REPO / "site"
"""The build output Zensical writes, which is what gets published."""

PAGE = SITE / "clients/index.html"
"""The Clients page in that output, and the proof that a build has happened."""

SOURCES = (
    REPO / "clients/python/global_wheat.py",
    REPO / "clients/js/global-wheat.js",
    REPO / "clients/java/GlobalWheat.java",
)
"""The files a participant downloads, one per language."""


def sync() -> list[str]:
    """Copy every client file beside the page that links to it, where it differs.

    Returns
    -------
    list of str
        The names of the files that were written, which is empty when the site already matched.

    Raises
    ------
    FileNotFoundError
        There is no built site to copy into, or a client file the page links to is missing from
        `clients/`.
    """
    if not PAGE.is_file():
        raise FileNotFoundError(
            f"{PAGE.relative_to(REPO)} is not there: build the site first, then run this"
        )
    written: list[str] = []
    for source in SOURCES:
        if not source.is_file():
            raise FileNotFoundError(
                f"the site links to {source.name}, which clients/ does not hold"
            )
        target = PAGE.parent / source.name
        if target.is_file() and target.read_bytes() == source.read_bytes():
            continue
        shutil.copyfile(source, target)
        written.append(source.name)
    return written


def main() -> int:
    """Report what the site was missing, so a build log says whether anything was stale.

    Returns
    -------
    int
        ``0`` when the site holds the files, ``1`` when it could not be done — one line in a build log
        rather than a traceback.
    """
    try:
        written = sync()
    except FileNotFoundError as missing:
        print(f"sync_clients: {missing}")
        return 1
    print("copied: " + ", ".join(written) if written else "the site already holds the clients")
    return 0


if __name__ == "__main__":
    sys.exit(main())
