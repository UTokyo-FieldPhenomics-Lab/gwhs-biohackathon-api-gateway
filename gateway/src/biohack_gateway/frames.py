"""The one mechanism by which this host lets the documentation site size a page it frames.

Two pages of this host are read inside `docs.global-wheat.com` — the API reference and the page a
participant runs a model from — and a cross-origin frame cannot be measured from the outside. Both
therefore carry the same reporter. It lives in `assets/frame_reporter.js` beside the modules that
serve the two pages, because a second copy of a messaging contract is exactly the kind of
duplicate `gateway.md` asks this package not to grow.

The reporter sends one number to one origin and does nothing else: it reads no data, holds no key,
and leaves the page working as it was when nothing listens. What the height means, and why it is
measured on a container rather than on the document, is in the script itself.
"""

from __future__ import annotations

from pathlib import Path

_ASSETS = Path(__file__).parent / "assets"

FRAME_REPORTER = (_ASSETS / "frame_reporter.js").read_text(encoding="utf-8")
"""Injected into a page `docs.global-wheat.com` frames, so the frame can be sized around it.

The alternative — a fixed height, or a scrollbar inside the frame — is what this replaces. It is a
same-origin-free mechanism: a message with a number, addressed to one origin, ignored by everyone
else, and the page works exactly as before when nothing listens. The three placeholders are filled
by :func:`frame_reporter`: the origin allowed both to frame the page and to hear from it, the
selector of the box whose bottom edge is the page's height, and an optional selector of a row above
it. The file is the script alone; the element that carries it is added here, so what sits in
`assets/` is one language and a linter can read it.
"""


def frame_reporter(parent_origin: str, container: str, extra: str = "") -> str:
    """Build the reporter for one framed page.

    Parameters
    ----------
    parent_origin : str
        The one origin allowed to frame the page, and the one window it reports its height to.
    container : str
        A selector for the element whose bottom edge is the page's height. A page that renders its
        content into a node of its own passes it, because a document does not shrink back to its
        content and would leave the frame permanently too tall.
    extra : str, optional
        A selector for a row a page keeps above that container, counted as part of the height.

    Returns
    -------
    str
        The script element, ready to be injected into the page's body.
    """
    script = (
        FRAME_REPORTER.replace("__PARENT_ORIGIN__", parent_origin)
        .replace("__CONTAINER__", container)
        .replace("__EXTRA__", extra)
    )
    return f"<script>{script}</script>"
