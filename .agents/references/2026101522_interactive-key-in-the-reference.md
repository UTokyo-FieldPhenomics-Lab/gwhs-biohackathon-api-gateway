# The reference takes a key

Date: 2026-10-15. Follows [`2026101516_docs-site-hosting.md`](2026101516_docs-site-hosting.md), which
built the site that frames the reference. The question this session answered was a participant's:
every call in `/docs` answered `401`, so nothing could be tried from the page.

## What the cause turned out to be

Not a broken gate, and not a missing input. `grep -n "security\|APIKeyHeader" gateway/src` found
nothing: the OpenAPI document declared **no security scheme at all**, so Swagger UI had nothing to
attach and did not even draw its Authorize button. Swagger UI carries a credential only for a scheme
the document declares *and* only for an operation that asks for it, which is why both halves are
written per operation in `_with_key_auth` rather than at the document root alone. `/health` says
`security: []`, because the contract must not claim a key for the one route that has none.

## What was built

- `_with_key_auth(schema)` injected through `app.openapi`, plus `API_KEY_SCHEME = "ApiKeyAuth"` beside
  `API_KEY_HEADER` so the scheme, the header, and the field all read one name.
- `swagger_ui_parameters={"persistAuthorization": True}` — FastAPI's defaults do not include it.
- `SWAGGER_KEY_BAR` (styles and markup) and `SWAGGER_KEY_BAR_SCRIPT` (the code), injected at two
  different points; see the traps below.
- `DOCS_PARENT_ORIGIN`, a `create_app` parameter, feeding both the `frame-ancestors` header on `/docs`
  and the origin the reporter posts to — one setting, one site.
- The dark stylesheet gained the servers block, which was the only region of the page still white.

## Four traps, and how each showed up

- **A field's script cannot live where its markup goes.** The markup belongs above `#swagger-ui`,
  which is *before* Swagger's bundle and init script; the script that drives it ran there, found no
  `ui`, and removed the field to avoid a control that swallows keys. Symptom: the bar was in the HTML
  and absent from the page, with the Authorize button still drawn. Markup and script are now separate
  constants injected at the two places each belongs.
- **`preauthorizeApiKey` does not persist.** It sets the key for the page and stops there; the dialog
  calls `authorizeWithPersistOption`, which is what writes `localStorage.authorized`. Measured:
  `localStorage` was empty after `preauthorizeApiKey` and held
  `{"ApiKeyAuth": {"value": …, "schema": …}}` after the persist variant. Forget uses
  `logoutWithPersistOption` for the same reason.
- **Swagger restores a remembered key *after* the page loads**, so a status line read once says "no
  key yet" while a key is in effect. The field re-reads the state for five seconds, then stops; only
  one boolean is read.
- **Restoring the `<div id="swagger-ui">` marker.** The markup constant is substituted for that exact
  marker, so the constant must put it back. When a rewrite of the constant dropped it, React mounted
  into nothing (`Minified React error #299`), the init script threw before `ui` was assigned, and the
  field died with `ui is not defined`. `test_the_reference_page_carries_a_key_field` now counts the
  marker, which is the test that would have caught it.

## Deliberate omissions

- The field does not validate the key with a probe request: that spends the participant's quota to
  answer a question the first Try it out answers anyway.
- The key is not stored by us. Swagger's own storage is the only copy, which is why the field shows
  *that* a key is in use rather than the key.
- Swagger's standard Authorize button is hidden — but only from the script that replaced it, so a page
  whose script failed keeps the door it always had. That also removes the reason the field exists: the
  built-in dialog is centred on the frame (`.dialog-ux .modal-ux` is `position: fixed; top: 50%`),
  and this frame is as tall as the page, so the dialog opens below the fold.

## Verification

- `pytest` (110), `mypy --strict`, and `ruff` are green; the new tests pin the scheme, the
  per-operation requirement, the `/health` exception, the field, persistence, the `frame-ancestors`
  header, and the configurable parent origin.
- A local app with a real token pool and one published index row, driven in Chromium through the
  actual UI: paste the key → the state line reports a key in use → Try it out on `/datasets` sends
  `-H 'X-API-Key: …'` and answers with the dataset payload; a reload keeps the key; Forget clears it
  and the padlocks unlock. No console messages, and the one Chrome "issue" noted (a form field
  without id/name) is Swagger's own media-type select.
- A harness parent on the configured origin framed the page: `frame-ancestors` let it load, the
  reported height included the field, and the site's palette message turned the framed page dark.
  Sending `light` from the harness left Swagger's own colours on a transparent page — an artefact of
  the harness's dark background, since the site's light surface is white; nothing was changed for it.

## Still open

The framed page has not been seen against the live host: the campus network drops the Elastic IP, so
`api.global-wheat.com/docs` is unreachable from here. The mechanism is verified locally and the
document is verifiable by `curl --resolve`, but the last click-through belongs to a browser that can
reach `api.*`.

## Second pass: where the field lives, and what a reader meets first

The field worked and looked like an artifact laid over the page: a grey panel of our own colours and
controls at the very top, above even the title. Two changes were asked for, and the second one turned
out to have a mechanism worth writing down.

- **Groups follow the document.** Swagger UI's `tagsSorter` defaults to `null`, its comparator then
  returns `null` for every pair, and the sort keeps the order it was given — which is the order of
  `paths` in the document, which is the order the routes are declared. So `/health` moved above the
  dataset router in `create_app` and the reference opens on the one call that needs no key. Its tag
  is now `liveness` rather than FastAPI's `default`. `test_the_reference_opens_on_the_call_that_needs_no_key`
  pins both, because both are the reader's first impression rather than formatting.
- **The field moved into Swagger's own band.** It is injected hidden and placed by script into
  `.scheme-container` — the strip the framework already keeps for page-wide settings, empty on this
  document because it declares no servers. React renders that band without knowing about this node,
  so a re-render can drop it; a `MutationObserver` on `#swagger-ui` puts it back. Verified by
  expanding an operation, running Try it out, and finding the field still in the band with the
  request carrying the key.
- **The row sits in the page's column, not the band's edge.** Prepending into the band put the field
  20 px left of the title and the calls: the band is full-bleed, while everything else lives in
  `.wrapper` (`max-width: 1460px; margin: 0 auto; padding: 0 20px`). So the row wears that class —
  the alignment is Swagger's own rule rather than a number of ours, and it holds on a wide screen
  where the column is centred. Measured: label, title, group heading and first call all at x=28.
  The two buttons are one flex item, because one of them alone on a second line reads as a mistake;
  on a phone they wrap together under the field, which is what the reader's screenshot asked for.
  The state line is what stays ours, since Swagger has no equivalent.
- **The styling stopped being ours.** The input is a plain `input[type=password]` under Swagger's own
  rules, the buttons carry Swagger's `btn authorize` and `btn` classes, and only layout is left in
  our block. Measured against a real Swagger parameter input: same border, radius, padding, font, and
  size; the primary button keeps the framework's green. The dark palette needed two additions for
  this — `input[type="password"]` and a `label` colour, the latter also fixing Swagger's own labels,
  which were still `#3b4151` on a `#1e2129` page.
- The landing page's sentence moved with the field: it says "the field above the call list" now.

## The live framed page, finally driven end to end

The one thing never verified from here was the real frame at <https://docs.global-wheat.com/interactive/>:
the campus network resolves `api.*` to its public address and that path is dropped, so the frame came up
empty in every earlier attempt. Three findings, in the order they mattered.

- **Reaching the host is a client-resolution problem, not a code problem.** Chromium with
  `--host-resolver-rules="MAP api.global-wheat.com gwhs-host"` reaches the API host over the tailnet
  address, and a direct load of `/docs` works immediately. No host change was needed.
- **Chrome then refused to let the site frame it**, with
  `net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`: a public site may not load a frame from a private
  address. That is entirely an artifact of the tailnet address — production `api.*` is public, where the
  check does not apply — and the harness disables it with
  `--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks,...`. Worth knowing before
  blaming the frame: a blank frame from a private address is Chrome's judgement, not the page's.
- **A real browser can wait, and reach into the frame.** `--virtual-time-budget` and `--dump-dom` could
  not see the child at all (virtual time expires the parent while the cross-origin child is still
  loading, so the height never arrives — even though the same flags show it for a localhost harness).
  Playwright does it properly, and can drive the cross-origin frame:

  ```python
  # uv run --with playwright python <this>; the system Chromium is enough, no download
  browser = p.chromium.launch(executable_path="/usr/bin/chromium",
                              args=["--host-resolver-rules=MAP api.global-wheat.com gwhs-host",
                                    "--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks"])
  page = browser.new_page(viewport={"width": 390, "height": 844})
  page.goto("https://docs.global-wheat.com/interactive/", wait_until="load")
  frame = page.frame_locator("#reference-frame iframe")   # cross-origin access, which is the point
  ```

  What it reported, against the deployed `951ff40`:

  | check | result |
  |---|---|
  | the site sized the frame from the child's report | `2093px`, not the `90vh` fallback |
  | the field's row | in `scheme-container`, label / title / group / first call all at x=28 |
  | groups | `['liveness', 'datasets']` |
  | a token pasted into the live field | "a key is in use — paste another to replace it" |
  | a call from inside the frame | `GET /datasets` answered with the live datasets |
  | collapse | frame `4000px → 2093px` — it shrinks on the live page, which was the oldest open question |
  | the theme channel from the site's origin | `light → dark`, framed page background `rgb(30, 33, 41)` |

  The only console entry is the site's own request for repository facts from `api.github.com`, which
  404s — a pre-existing cosmetic thing in the site config, nothing to do with the reference.
