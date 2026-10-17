"""The page a participant runs one model from, served by the API host itself.

Serving it from here is what makes the live call possible at all: the page is same-origin with
the API, so **no CORS is involved anywhere**, and the key it uses is one the reference page and
this page share — one entry on the API's origin, set or cleared from either. The documentation
site frames this page and does nothing else: the site never sees the key.

The page is four rows and nothing more: the image, either a published row or an upload — it comes
from the dataset surface and needs no model, so it is asked for and previewed first — then a model
selector filled from ``GET /models``, the model's own parameters, and the result on a canvas, with
Run at the end of that last row. **The controls are
built from the model's declaration**, never hard-coded, which is the point of the whole design:
a model this page has never heard of works without a change to it, so a model it cannot render
is an incomplete declaration rather than a page that is not clever enough.

Two facts from the browser shape the drawing code, and both are in the script where they bite.
``data.global-wheat.com`` sends no ``access-control-allow-origin`` header, so the image is loaded
without ``crossorigin``, the canvas is tainted, and **no pixel may be read back** — the mask is
therefore composited from an offscreen canvas rather than read off the visible one, and the
``Σ mask = area`` check counts the decoded array instead of the canvas. And the decoder is the
documentation site's own published file, loaded from there rather than copied, so the demo and the
tutorial cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse

from biohack_gateway.frames import frame_reporter

PLAY_PATH = "/play"
"""The path the page is served at, and the one name for it in the application.

Kept here rather than spelled out twice, because the page's route and its entry in the open
paths are the same decision made in two places.
"""

_ASSETS = Path(__file__).parent / "assets"

PLAY_PAGE = (
    (_ASSETS / "play.html")
    .read_text(encoding="utf-8")
    .replace("__PLAY_CSS__", (_ASSETS / "play.css").read_text(encoding="utf-8"))
    .replace("__API_KEY_JS__", (_ASSETS / "api_key.js").read_text(encoding="utf-8"))
    .replace("__PLAY_JS__", (_ASSETS / "play.js").read_text(encoding="utf-8"))
)
"""The page's markup, with its own stylesheet and scripts inlined where the placeholders are.

Three files, one language each, so the page is edited as a page and this module is Python: what
makes the five rows work is `assets/play.js`, what they look like is `assets/play.css`, and neither
is a string in this file. `assets/api_key.js` is the one store both framed pages remember a key in,
so it is inlined here as well as in the reference. Two placeholders are left for when the page is
served, because what goes in them depends on the site that asked: the decoder's URL, and the frame's
height reporter.
"""


def play_page(parent_origin: str) -> HTMLResponse:
    """Build the page a participant runs one model from.

    Parameters
    ----------
    parent_origin : str
        The one site allowed to frame the page, which is also the site this page loads the decoder
        from: the documentation site publishes that file, and a copy here would be a second decoder
        to keep true. The frame's height reporter addresses the same origin.

    Returns
    -------
    HTMLResponse
        The page, carrying the `frame-ancestors` that names that one site and nothing else. The
        page is where a reader types their key, and a field for a secret in a page that
        anything may embed is a field that anything may overlay.
    """
    page = PLAY_PAGE.replace(
        "__DECODER__", f"{parent_origin}/post-processing/rle_decode.js"
    ).replace("__REPORTER__", frame_reporter(parent_origin, "#play"))
    return HTMLResponse(
        page, headers={"Content-Security-Policy": f"frame-ancestors {parent_origin}"}
    )
