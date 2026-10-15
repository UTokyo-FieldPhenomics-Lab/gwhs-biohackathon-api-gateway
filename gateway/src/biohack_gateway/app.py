"""Global Wheat API gateway application.

The surface is small and every part of it is deliberate. ``/health`` and the reference page answer
without a key because they carry no data — liveness, and a contract that is public in this
repository anyway — and the reference is also where a reader puts the key that unlocks everything
else. Every other path, including paths that do not exist, needs a participant token, because the
people this serves are somewhere else on the internet and nothing but that token stands between them
and the host.

Key authentication, the error contract, and liveness live here. The dataset surface is included
from here, and the model surface will arrive the same way: both inherit the gate by adding a route
rather than by remembering to.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from biohack_gateway.auth import Authenticator
from biohack_gateway.dataset_index import DEFAULT_INDEX_DIR, load_datasets
from biohack_gateway.datasets import DEFAULT_DATA_BASE_URL, create_dataset_router
from biohack_gateway.tokens import DEFAULT_TOKENS_FILE, TokenPoolError

logger = logging.getLogger(__name__)

# uvicorn configures loggers of its own and never the root one, so without this the lines this
# package writes — the pool it read, the team a request is attributed to — are discarded before they
# reach journalctl. That attribution is what replaces a counted quota here, so it has to be visible.
# `basicConfig` leaves an existing root configuration alone, so a deployment that passes
# `--log-config` still wins.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

APP_VERSION = installed_version("biohack-gateway")
"""The installed package's version, so ``/health`` reports what is actually running, not what is
in the checkout it was started from."""

API_KEY_HEADER = "X-API-Key"
"""Where a participant's token arrives."""

API_KEY_SCHEME = "ApiKeyAuth"
"""The name the contract gives that header.

Swagger UI carries a credential into a request only for a scheme the document declares, so without
this the reference lists every endpoint and can try none of them. The name is arbitrary, the header
is not, and both are read from here rather than written out again.
"""

DOCS_PARENT_ORIGIN = "https://docs.global-wheat.com"
"""The one site allowed to frame the reference page, and the one window it reports its height to.

One origin rather than two settings: the page tells the browser who may frame it, and it addresses
the height it reports to the same site. A harness that frames a local app passes its own.
"""

OPEN_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})
"""The paths that answer without a key.

Both entries earn the exception the same way: they say nothing about the data and exist for someone
who has no key yet, and the documentation pages cannot be reached from a browser at all if the page
itself needs a header. Every other path — including ones that do not exist — is behind the gate.
"""

_DARK_STYLE_PATH = Path(__file__).with_name("swagger_dark.css")

SWAGGER_DARK_STYLE = f"<style>{_DARK_STYLE_PATH.read_text(encoding='utf-8')}</style>"
"""Swagger UI in the documentation site's dark palette, carried by the page itself.

Swagger UI ships no dark theme — the CDN serves one stylesheet and no dark variant — so this one
lives beside this module and is read at import. It applies only when the page has been told which
side it is on: by the `?scheme=` it was framed with, by the site's message when the reader switches,
or by the operating system's preference when the page is opened on its own.
"""

SWAGGER_FRAME_REPORTER = """
<script>
  // The documentation site frames this page, and a cross-origin frame cannot be measured from
  // outside: the page reports its own height so the frame grows to its content and the reader
  // scrolls one page instead of two nested ones. Nothing here reads or sends anything but that
  // number, and only to the documentation site.
  //
  // The height is the bottom edge of the Swagger UI container, not `documentElement.scrollHeight`:
  // observing `documentElement` catches a section expanding and misses it collapsing, because a
  // document's box does not shrink back to its content while the frame stays tall. The container
  // does shrink, so it is what gets observed — together with the class changes Swagger UI makes
  // when a section is toggled, which is what a collapse looks like. The key field above the
  // container counts as well, or the frame would cut off the one control a reader needs first.
  // The same channel carries the theme: the site says which side of the palette it is on, as a
  // query parameter for the first paint and as a message whenever the reader switches.
  const SITE_ORIGIN = "__PARENT_ORIGIN__";
  const apply_scheme = (scheme) => {
    if (scheme === "dark" || scheme === "light") document.documentElement.dataset.scheme = scheme;
  };
  apply_scheme(
    new URLSearchParams(location.search).get("scheme")
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  );

  if (window.parent !== window) {
    // Framed: the site around the frame owns the height, so this page must not scroll — a scrollbar
    // here would also narrow the viewport, reflow the text a few pixels taller, and keep itself
    // alive. Opened directly, nothing is hidden and the page scrolls as Swagger UI intends.
    document.documentElement.style.overflow = "hidden";
    document.body.style.overflow = "hidden";
  }
  const report_height = () => requestAnimationFrame(() => {
    // Opened on its own there is nobody to size, and a message addressed to the site's origin
    // from a page that is not framed is a console warning and nothing else.
    if (window.parent === window) return;
    const ui = document.querySelector("#swagger-ui") || document.body;
    const bar = document.querySelector("#key-bar");
    const barBottom = bar ? bar.getBoundingClientRect().bottom : 0;
    const bottom = Math.max(ui.getBoundingClientRect().bottom, barBottom);
    window.parent.postMessage({docsHeight: Math.ceil(bottom + window.scrollY)}, SITE_ORIGIN);
  });
  window.addEventListener("message", (event) => {
    if (event.origin !== SITE_ORIGIN || !event.data) return;
    apply_scheme(event.data.scheme);
  });
  const observed = document.querySelector("#swagger-ui") || document.body;
  new ResizeObserver(report_height).observe(observed);
  new MutationObserver(report_height).observe(document.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden"],
  });
  window.addEventListener("load", report_height);
  window.addEventListener("resize", report_height);
</script>
"""
"""Injected into the Swagger UI page so `docs.global-wheat.com` can size the frame around it.

The alternative — a fixed height, or a scrollbar inside the frame — is what this replaces. It is a
same-origin-free mechanism: a message with a number, addressed to one origin, ignored by everyone
else, and the page works exactly as before when nothing listens. `__PARENT_ORIGIN__` is replaced
with the app's configured parent origin, so the page names one site for both the frame and the
message.
"""


SWAGGER_KEY_BAR = """
<style>
  /* Layout only: the type, the input, and the buttons are Swagger UI's own, and so is the column
     below — `wrapper` is the class the framework puts its own content in, which is what keeps this
     row lined up with the title and the calls rather than flush to the band it sits in. The one
     thing Swagger has no equivalent for is the state line. */
  #key-bar form {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 0.75rem;
    align-items: center;
  }
  #key-bar label { margin: 0; font-size: 14px; font-weight: 700; }
  #key-bar input { flex: 1 1 14rem; min-width: 0; margin: 0; }
  /* The two buttons wrap together or not at all: one of them alone on a second line reads as a
     mistake, and on a phone they belong on their own line under the field. */
  #key-bar .key-buttons { display: inline-flex; flex-wrap: wrap; gap: 0.5rem; }
  #key-bar .key-state { display: block; margin-top: 0.5rem; color: #6b7280; }
  #key-bar .key-state[data-set="yes"] { color: #1f7a4d; }
  html[data-scheme="dark"] #key-bar .key-state { color: #9aa4b2; }
  html[data-scheme="dark"] #key-bar .key-state[data-set="yes"] { color: #4ec07f; }
  /* One way in, and only once the field below has proved it works: a reader who found the standard
     button instead would meet a dialog that opens at the centre of a frame as tall as the page. */
  html.key-bar-ready .swagger-ui .auth-wrapper { display: none; }
</style>
<div id="key-bar" class="wrapper" hidden>
  <form id="key-form" autocomplete="off">
    <label for="key-input">Your key</label>
    <input
      id="key-input"
      name="key"
      type="password"
      autocomplete="off"
      spellcheck="false"
      placeholder="__HEADER__"
      aria-describedby="key-state"
    />
    <span class="key-buttons">
      <button type="submit" class="btn authorize">Use key</button>
      <button id="key-forget" type="button" class="btn">Forget</button>
    </span>
  </form>
  <span id="key-state" class="key-state" role="status"></span>
</div>
<div id="swagger-ui">
""".replace("__HEADER__", API_KEY_HEADER)
"""The field a participant puts their key into, and the little styling it adds.

It is injected hidden — it belongs inside Swagger UI's own servers band, between the description
and the call list, and a field that appeared at the top of the page first would be a flash of the
wrong thing. The script moves it there once the page has rendered, and takes it away entirely if it
cannot: what this row shows, Swagger UI already styles.
"""

SWAGGER_KEY_BAR_SCRIPT = """
<script>
  (() => {
    // The reference is read inside the documentation site's frame, and a framed page cannot show
    // Swagger UI's own Authorize dialog where the reader is looking: the frame is as tall as the
    // page, so a centred dialog opens somewhere below the fold. This field is where a key goes
    // instead — it calls what the dialog calls, in the place a reader looks for page-wide settings.
    const SCHEME = "__SCHEME__";
    const bar = document.getElementById("key-bar");
    const form = document.getElementById("key-form");
    const input = document.getElementById("key-input");
    const state = document.getElementById("key-state");
    if (!bar || !form || !input || !state || typeof ui === "undefined") {
      // No bar rather than a dead one: without Swagger's own object a field would swallow keys.
      if (bar) bar.remove();
      return;
    }

    // Swagger's servers band is the strip between the description and the call list, where a reader
    // expects page-wide settings to live — and the band whose background makes this row part of the
    // page rather than something laid over it. React renders that band and has never heard of this
    // node, so a re-render can drop it; that is what the observer at the end is for.
    const anchor = () =>
      document.querySelector("#swagger-ui .scheme-container") ||
      document.querySelector("#swagger-ui .information-container");
    const place = () => {
      const target = anchor();
      if (!target) return;
      if (bar.parentElement !== target) target.prepend(bar);
      bar.hidden = false;
    };

    // Read Swagger's state rather than keeping a copy: the key is stored by Swagger (the page asks
    // it to persist one), and one copy of a secret is enough. A failure here says nothing rather
    // than something wrong.
    const describe = () => {
      try {
        const held = ui.authSelectors.authorized().get(SCHEME);
        state.dataset.set = held ? "yes" : "no";
        state.textContent = held
          ? "a key is in use — paste another to replace it"
          : "no key yet — paste your team's token, and every call below can be tried";
      } catch {
        state.textContent = "";
      }
    };

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      // Swagger remembers a key only when it is handed in the variant its own dialog uses, with the
      // definition it applies to; `preauthorizeApiKey` sets it for this page and loses it on the
      // next visit, which is the one thing this page asks Swagger to do for it.
      const schema = ui.specSelectors.securityDefinitions().get(SCHEME);
      if (schema && ui.authActions.authorizeWithPersistOption) {
        ui.authActions.authorizeWithPersistOption({[SCHEME]: {value: value, schema: schema}});
      } else {
        ui.preauthorizeApiKey(SCHEME, value);
      }
      input.value = "";
      describe();
    });
    document.getElementById("key-forget").addEventListener("click", () => {
      const forget = ui.authActions.logoutWithPersistOption || ui.authActions.logout;
      forget([SCHEME]);
      describe();
    });

    // Placed, and kept placed: anything Swagger re-renders inside its container can take this node
    // with it, and putting it back is two selectors and a comparison. The same observation keeps
    // the state line true, because the two things that change it are re-renders of this container —
    // the specification arriving, and Swagger restoring a remembered key when it mounts. `load`
    // covers a restore that lands without one, so nothing here has to poll for either.
    const settle = () => {
      place();
      describe();
    };
    new MutationObserver(settle).observe(document.querySelector("#swagger-ui"), {
      childList: true,
      subtree: true,
    });
    window.addEventListener("load", describe);
    settle();
    document.documentElement.classList.add("key-bar-ready");
  })();
</script>
""".replace("__SCHEME__", API_KEY_SCHEME)
"""What makes the field work, injected at the end of the body where Swagger UI exists.

It hands the key to the same action Swagger's own dialog hands it to, so the request carries the
header and the browser remembers the key between visits; `preauthorizeApiKey` alone would do the
first and not the second. A field that cannot do both removes itself, and only a field that did
both hides the standard Authorize button — a page whose script did not run keeps the door it always
had.
"""


def _with_key_auth(schema: dict[str, Any]) -> dict[str, Any]:
    """Declare in the contract the key the gate already demands.

    Every operation but liveness requires a token, and the gate enforces that whatever the document
    says — so this changes nothing about who gets in. What it changes is whether the reference can
    be used: Swagger UI carries a credential into a request only for a scheme the document declares
    and only for an operation that asks for it, so an undeclared key means every Try it out answers
    ``401`` however carefully the reader pastes their token.

    The requirement is written per operation rather than left to the document root, because that is
    what Swagger UI reads. An operation that declares its own security keeps it: that is how
    liveness says it needs none.

    Parameters
    ----------
    schema : dict
        The document FastAPI generated, mutated in place.

    Returns
    -------
    dict
        The same document, with the scheme and the requirements added.
    """
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})[API_KEY_SCHEME] = {
        "type": "apiKey",
        "in": "header",
        "name": API_KEY_HEADER,
    }
    required: list[dict[str, list[str]]] = [{API_KEY_SCHEME: []}]
    schema["security"] = required
    for path in schema.get("paths", {}).values():
        for method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
            operation = path.get(method)
            if operation is not None and "security" not in operation:
                operation["security"] = required
    return schema


class ErrorCode(StrEnum):
    """Machine-readable error codes shared by every endpoint.

    The set is the frozen initial contract and grows additively: adding a member is
    non-breaking, removing or renaming one is not.
    """

    INVALID_REQUEST = "invalid_request"
    INVALID_KEY = "invalid_key"
    NOT_FOUND = "not_found"
    QUOTA_EXCEEDED = "quota_exceeded"
    UPSTREAM_ERROR = "upstream_error"
    UNAVAILABLE = "unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    INTERNAL_ERROR = "internal_error"


_CODES_BY_STATUS: dict[int, ErrorCode] = {
    401: ErrorCode.INVALID_KEY,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.INVALID_REQUEST,
    429: ErrorCode.QUOTA_EXCEEDED,
    503: ErrorCode.UNAVAILABLE,
}
"""The codes the framework's own rejections map onto.

Only the ones we expect to raise ourselves or to inherit from the router are named; anything else
falls back by class of status, which is the part of the contract a caller can act on anyway.
"""


class ErrorDetail(BaseModel):
    """The error payload nested inside :class:`ErrorResponse`.

    Attributes
    ----------
    code : ErrorCode
        Stable machine-readable discriminant.
    message : str
        Human-readable explanation, safe to surface to the caller.
    """

    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """The one error body shape for the whole API.

    Attributes
    ----------
    error : ErrorDetail
        The error payload, nested so the top level can grow without touching it.
    """

    error: ErrorDetail


class HealthResponse(BaseModel):
    """What the gateway says about itself without a key.

    Attributes
    ----------
    status : Literal["ok"]
        Whether the process is serving. A gateway that cannot say so does not answer at all.
    version : str
        The version of the installed package that is answering.
    """

    status: Literal["ok"]
    version: str


def _code_for(status_code: int) -> ErrorCode:
    """Choose the code for a rejection raised by the framework.

    Parameters
    ----------
    status_code : int
        The status the framework or a route asked for.

    Returns
    -------
    ErrorCode
        The named code when the status is one we plan for, otherwise ``internal_error`` for a
        server-side problem and ``invalid_request`` for a client-side one.
    """
    if code := _CODES_BY_STATUS.get(status_code):
        return code
    return ErrorCode.INTERNAL_ERROR if status_code >= 500 else ErrorCode.INVALID_REQUEST


def _error(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    """Build the one error body.

    Every refusal in this application goes through here, which is what keeps the shape single.

    Parameters
    ----------
    status_code : int
        The HTTP status to answer with.
    code : ErrorCode
        The machine-readable code.
    message : str
        The explanation. It is shown to the caller, so it must never carry a token, a backend
        address, or anything else the caller should not learn.

    Returns
    -------
    JSONResponse
        The serialized :class:`ErrorResponse`.
    """
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_app(
    tokens_path: Path = DEFAULT_TOKENS_FILE,
    index_dir: Path = DEFAULT_INDEX_DIR,
    data_base_url: str = DEFAULT_DATA_BASE_URL,
    docs_parent_origin: str = DOCS_PARENT_ORIGIN,
) -> FastAPI:
    """Build the application.

    Every host setting is a parameter rather than a global so that a test can point an app at
    throwaway files, and so that nothing in this module depends on the host's layout. The dataset
    index is loaded here, once, which is what makes a republish arrive as a restart.

    Parameters
    ----------
    tokens_path : Path, optional
        The participant token pool to authenticate against.
    index_dir : Path, optional
        The directory of `<dataset>.db` files the dataset surface answers from. It need not exist.
    data_base_url : str, optional
        The public distribution the image addresses point at.
    docs_parent_origin : str, optional
        The one origin allowed to frame the reference page, which is also the window it reports its
        height to. A harness that frames a local app passes its own origin here.

    Returns
    -------
    FastAPI
        The gateway application, ready for uvicorn.
    """
    app = FastAPI(
        title="Global Wheat API",
        version=APP_VERSION,
        description=(
            "Global Wheat dataset and model API for the BioHackathon. "
            "Guides and examples: https://docs.global-wheat.com"
        ),
        docs_url=None,  # registered below instead, so the page can carry the frame reporter
    )

    # Declared before the dataset surface on purpose. The reference groups its calls in the order
    # the document lists its paths — Swagger UI's tag sorter keeps what it is given — so where this
    # route is registered decides what a reader meets first: the one call that needs no key, in a
    # group whose name says what it is rather than FastAPI's placeholder.
    @app.get(
        "/health",
        response_model=HealthResponse,
        summary="Liveness",
        description="Answers without a key, so a monitor or the deploy script can ask it.",
        tags=["liveness"],
        openapi_extra={"security": []},  # the one operation the contract says needs no key
    )
    async def health() -> HealthResponse:
        """Report that the process is serving.

        Returns
        -------
        HealthResponse
            The status and the version of the package that is answering.
        """
        return HealthResponse(status="ok", version=APP_VERSION)

    authenticator = Authenticator(tokens_path)
    app.include_router(create_dataset_router(load_datasets(index_dir), data_base_url))

    document = app.openapi

    def openapi_with_key_auth() -> dict[str, Any]:
        """Answer the contract with the key scheme the reference needs to be usable.

        Returns
        -------
        dict
            The document FastAPI generated, declared to require the header the gate reads.
        """
        if app.openapi_schema is None:
            app.openapi_schema = _with_key_auth(document())
        return app.openapi_schema

    app.openapi = openapi_with_key_auth  # type: ignore[method-assign]

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        """Answer the interactive reference, in the shape the documentation site's frame needs.

        Returns
        -------
        HTMLResponse
            FastAPI's own Swagger UI page with three additions: a field for the reader's key, the
            dark palette, and the script that reports this page's height to the site that frames it.
            The page is otherwise untouched, and a reader who opens `/docs` directly sees the same
            reference with the same field.
        """
        page = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — reference",
            swagger_ui_parameters={"persistAuthorization": True},
        )
        html = bytes(page.body).decode()
        html = html.replace('<div id="swagger-ui">', SWAGGER_KEY_BAR, 1)
        reporter = SWAGGER_FRAME_REPORTER.replace("__PARENT_ORIGIN__", docs_parent_origin)
        return HTMLResponse(
            html.replace(
                "</body>", f"{SWAGGER_DARK_STYLE}{SWAGGER_KEY_BAR_SCRIPT}{reporter}</body>"
            ),
            headers={"Content-Security-Policy": f"frame-ancestors {docs_parent_origin}"},
        )

    @app.middleware("http")
    async def require_key(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Let the open paths through and demand a token everywhere else.

        Fail closed on purpose: the gate is applied to the request, not to a list of routes, so an
        endpoint added later is protected by existing. The team the token belongs to is recorded on
        the request and logged once, which is the attribution that stands in for a counted quota.

        Parameters
        ----------
        request : Request
            The incoming request.
        call_next : RequestResponseEndpoint
            The rest of the stack.

        Returns
        -------
        Response
            The application's answer, or the refusal this middleware built.
        """
        path = request.url.path.rstrip("/") or "/"
        if path in OPEN_PATHS:
            return await call_next(request)

        try:
            team_id = authenticator.team_for(request.headers.get(API_KEY_HEADER, ""))
        except TokenPoolError:
            # Nobody can be authenticated until the file parses, and the caller is told only
            # that the gateway cannot serve them. The pool names its own problem where it is
            # read — once per change, rather than once per request as it would be here.
            return _error(
                503,
                ErrorCode.UNAVAILABLE,
                "the gateway cannot authenticate anyone right now",
            )

        if team_id is None:
            # The presented value is not echoed: it may be a typo of a real token.
            return _error(
                401,
                ErrorCode.INVALID_KEY,
                f"send a participant token in the {API_KEY_HEADER} header",
            )

        request.state.team_id = team_id
        logger.info("%s %s team=%s", request.method, request.url.path, team_id)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """Answer invalid input with ``400`` instead of FastAPI's default ``422``.

        The contract has exactly one "bad parameter" response: ``400`` with an
        ``ErrorResponse`` body. Overriding the handler here keeps the generated API
        reference honest, since FastAPI would otherwise advertise ``422`` on every
        endpoint that accepts parameters.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : RequestValidationError
            The validation failure raised by FastAPI.

        Returns
        -------
        JSONResponse
            A ``400`` response carrying :class:`ErrorResponse`.
        """
        errors = exc.errors()
        message = errors[0]["msg"] if errors else "invalid request"
        return _error(400, ErrorCode.INVALID_REQUEST, message)

    @app.exception_handler(StarletteHTTPException)
    async def rejection(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Give the framework's own rejections — 404, 405, and whatever a route raises — one shape.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : StarletteHTTPException
            The rejection, carrying the status the framework or a route chose.

        Returns
        -------
        JSONResponse
            The status that was asked for, with the body this contract defines.
        """
        return _error(exc.status_code, _code_for(exc.status_code), str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Answer a crash with the contract rather than with a traceback.

        The exception is not logged here: the server re-raises it after this handler answers, so its
        own log already carries the traceback, and printing it twice helps nobody.

        Parameters
        ----------
        request : Request
            The incoming request, unused but required by the handler signature.
        exc : Exception
            The failure. Its text is deliberately not passed on.

        Returns
        -------
        JSONResponse
            A ``500`` response carrying :class:`ErrorResponse`.
        """
        return _error(500, ErrorCode.INTERNAL_ERROR, "unexpected server error")

    return app


app = create_app()
"""The object uvicorn serves: ``uvicorn biohack_gateway.app:app``."""
