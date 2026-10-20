"""The Content-Security-Policy the two pages that hold a key carry.

Both pages are served by this host, and both remember the key in one entry of this host's storage,
so any script that runs on either page can read it. The policy is what turns "any script" into a
list: this host, the one documentation site the model page imports its decoder from, and the inline
blocks this host writes itself — each admitted by a nonce minted for one response rather than by
``'unsafe-inline'``, which would admit every block a script could inject.

A hash per inline block is the other way to admit them, and it would be a static header. A nonce is
used instead because this repository declines to compute content hashes at all
(``.agents/rules/project.md``) and because a nonce cannot drift from the page it was minted for.

Styles are not scripts. Swagger UI writes style attributes while it renders and both pages carry
their stylesheet inline, so ``style-src`` allows inline style and nothing more: an inline style
cannot execute, and refusing it would break a page without protecting anything.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from urllib.parse import urlsplit

_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)", re.IGNORECASE)
"""An opening ``<script>`` tag that loads nothing, and so needs the response's nonce.

The lookahead is what keeps a ``src`` script out: the external bundle is allowed by origin, and a
nonce on it would say nothing.
"""

NONCE_BYTES = 16
"""Entropy in a nonce. It need only outguess one document's lifetime — the page is never cached."""


def new_nonce() -> str:
    """Mint the value one page's inline scripts are admitted by.

    Returns
    -------
    str
        A URL-safe random string, unique to one response.
    """
    return secrets.token_urlsafe(NONCE_BYTES)


def with_nonce(html: str, nonce: str) -> str:
    """Mark every inline script in a page with the nonce its policy names.

    Parameters
    ----------
    html : str
        The finished page, after every placeholder has been filled.
    nonce : str
        The value :func:`new_nonce` returned for this response.

    Returns
    -------
    str
        The same page with ``nonce="..."`` on each inline ``<script>`` and on nothing else.
    """
    return _INLINE_SCRIPT.sub(f'<script nonce="{nonce}"', html)


def origin_of(url: str) -> str:
    """Reduce a URL to the origin a policy source expression can name.

    Parameters
    ----------
    url : str
        A base URL from the application's settings, which may carry a path.

    Returns
    -------
    str
        ``scheme://host[:port]``, or an empty string when the value names no origin. Empty is what a
        caller skips, so an unset setting adds no source rather than a broken one.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def policy(
    *,
    nonce: str,
    frame_ancestors: str,
    script_origins: Iterable[str] = (),
    image_origins: Iterable[str] = (),
    blob_images: bool = False,
) -> str:
    """Build the header value for one page.

    Parameters
    ----------
    nonce : str
        The value :func:`with_nonce` put on this page's inline scripts.
    frame_ancestors : str
        The one origin allowed to embed the page. The same origin is also the window the reporter
        addresses, and on the model page it is where the decoder is loaded from.
    script_origins : sequence of str, optional
        Further origins whose scripts the page may run. The model page names the documentation site
        here because its decoder is published there and a copy in this repository would be a second
        decoder to keep true.
    image_origins : sequence of str, optional
        Further origins the page may draw an image from — the public data distribution, which is
        where a published row's picture comes from.
    blob_images : bool, default False
        Whether the page previews a file the reader chose. The model page does, and a preview is a
        ``blob:`` URL made by the page itself.

    Returns
    -------
    str
        A single-line header value: one policy, no newline, every directive explicit.
    """
    images = ["'self'", "data:"]
    if blob_images:
        images.append("blob:")
    images.extend(image_origins)
    directives = (
        ("default-src", ["'none'"]),
        ("script-src", ["'self'", f"'nonce-{nonce}'", *script_origins]),
        # Inline style only, and never inline script: the two are separate directives precisely
        # because one can execute and the other cannot.
        ("style-src", ["'self'", "'unsafe-inline'"]),
        ("img-src", images),
        ("connect-src", ["'self'"]),
        ("font-src", ["'self'"]),
        ("frame-ancestors", [frame_ancestors]),
        ("base-uri", ["'none'"]),
        ("form-action", ["'self'"]),
    )
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives)
