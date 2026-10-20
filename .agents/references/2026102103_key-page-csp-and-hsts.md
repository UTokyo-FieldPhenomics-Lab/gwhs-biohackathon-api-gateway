# The key pages stop running somebody else's code, and get an HSTS

2026-10-21. A review of what a public repository exposes ended at the two pages a participant types
a token into. Both were safe by construction — the key travels in a header, never a URL, and the
page never reflects input — but both loaded a script from a host nobody here controls, and the key
sits in that origin's `localStorage`, so any script on either page can read it. Closed here, with a
policy that names what may run rather than trusting what was loaded.

## What changed

- **Swagger UI is vendored.** `assets/swagger-ui-bundle.js` and `assets/swagger-ui.css`, release
  **5.34.0**, plus the notices file the bundle's first line names. `get_swagger_ui_html` points at
  three explicit routes under `/docs/`, so the browser fetches them from this host. The favicon
  became `data:,` — the default pointed at `fastapi.tiangolo.com`, one more third-party request on
  the key page.
- **`/redoc` is gone.** `redoc_url=None`. It duplicated `/docs`, loaded `redoc@next` from a floating
  CDN tag, and sat on the same origin as the key store — one page nobody linked and one way for a
  third party's script to reach every key typed on this host.
- **`csp.py`** builds one policy per response: `script-src 'self' 'nonce-…'` (plus, on `/play`, the
  documentation origin the decoder is published from), `style-src 'self' 'unsafe-inline'`,
  `img-src 'self' data: blob: <data origin>`, `connect-src 'self'`, `frame-ancestors <parent>`,
  `base-uri 'none'`, `form-action 'self'`, `default-src 'none'`.
- **A nonce, not hashes.** `with_nonce` marks every inline `<script>` in the finished page; the same
  value goes in the header. Hashes would be static, but this repository declines to compute content
  hashes at all (`rules/project.md`), and a nonce cannot drift from the page it was minted for.
- **HSTS in `deploy/Caddyfile`**, `max-age=86400` — a day, not a year, and no `preload`: the host is
  built for one event, and a browser that remembers the name for a year would refuse plain HTTP to
  it long after. Deliberately not `includeSubDomains`; there are none.
- **`encode zstd gzip`**, because the reference now loads a 1.6 MB bundle itself instead of a CDN's
  precompressed copy.

## What was measured

- `uv run pytest` — 251 passed. New tests: the reference loads nothing from `cdn.jsdelivr.net` or
  `fastapi.tiangolo.com`; every inline script carries the nonce the header names; the three assets
  answer without a key and carry their own content types; `/redoc` is 401 like any unknown path;
  `/play` admits exactly the docs origin for scripts and `data.global-wheat.com` + `blob:` for
  images.
- `uv run mypy` — clean, `uv run ruff check` and `ruff format --check` clean.
- **In a browser, against a local uvicorn on 8123:** `/docs` renders with the key bar where the
  script moves it, `swagger-ui-bundle.js` and `swagger-ui.css` answer `200` from this host, the
  favicon is `data:,`, and the console has no messages at all — no CSP violation, no 404. A
  same-origin `fetch('/health')` runs under `connect-src 'self'`. `/play` loads
  `https://docs.global-wheat.com/clients/global-wheat.js` (`200`) under `script-src`, renders its
  four rows, and logs nothing.

## What broke, and what to know next time

- **The bundle cannot be inlined blindly.** `_ASSETS` carried the rule "read at import and inlined,
  never mounted"; a 1.7 MB script in every reference response is worse than a route that names it.
  The docstring now says which files are the exception and why, and `gateway.md`'s list of paths
  that need no key was corrected — the page cannot send a header for its own subresources.
- **`script-src 'self'` alone would have broken both pages.** `/docs` carries three inline scripts
  and two inline style blocks; `/play` carries two inline module scripts and imports the decoder
  from the docs origin. The CSP had to be built after the page was assembled, from what is actually
  in it.
- **The `AGENTS.md` pre-commit check fires on the vendored bundle, and on nothing else.** Its one
  minified line holds `127.0.0.1`, `198.51.100.42` (Swagger's example generator) and three
  `key:"componentDidUpdate"`-style React lifecycle names. Read it as a file rather than as added
  lines: run the check with the asset excluded, and look inside the asset with `grep -oP` when a
  match needs explaining.
- **Swagger UI does not need `unsafe-eval`.** The bundle has exactly one `new Function`, webpack's
  `globalThis` fallback inside a `try`/`catch` that never runs in a current browser. Worth
  re-checking on the next version bump.
- `caddy validate` was not available on this machine, so the Caddyfile change is syntax-checked by
  reading only. Re-check on the host with `caddy validate --config /etc/caddy/Caddyfile` before
  `systemctl reload caddy`.
