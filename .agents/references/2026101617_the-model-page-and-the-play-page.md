# The model page, the page that runs a model, and the two bugs a browser found

Session of 2026-10-16. Built the documentation site's `Models` page with its demo panel, and
`/play` on the gateway — the two deliverables of
[`../plans/2026101617_play-page.md`](../plans/2026101617_play-page.md) — plus the frame reporter both
framed pages share and the `assets/` split the user asked for while it was in progress. Constraints
are in [`../rules/gateway.md`](../rules/gateway.md) and [`../rules/docs.md`](../rules/docs.md); this
file is what happened.

Status: both pages are in place and verified in a browser against a stand-in container. Three steps
of the plan are still open, and all three need something only the host or the user has: the recorded
call the demo panel replays, a rehearsal against a real GPU backend, and the check that a second
model in the host's list needs no page change.

## What was built

- `gateway/src/biohack_gateway/assets/` — the page material, one language to a file:
  `play.html`, `play.js`, `play.css`, `swagger_key_bar.html`, `swagger_key_bar.js`,
  `swagger_dark.css`, `swagger_trim.css`, `frame_reporter.js`. Read at import and inlined; no
  mount, so nothing depends on the working directory.
- `gateway/src/biohack_gateway/play.py` — the page's route, its assembly, and its CSP.
  `frames.py` — one copy of the height reporter, parameterised by container selector.
- `docs/content/models/index.md` and `demo.js` — the model's map, its parameters, the two calls, the
  frame, the recorded-call panel, and the measured behaviour of the model at 1008.
- `OPEN_PATHS` gained `/play`; `docs/content/api/frame.js` now matches `iframe[data-api-frame]`
  instead of `#reference-frame iframe`, so one file sizes both frames.

Verified: a wheel built from `gateway/` contains `biohack_gateway/assets/*` — hatchling ships every
file under the package directory, not only `.py` — and both served pages are byte-identical to the
pre-split pages ignoring whitespace, which was the check that the split changed nothing.

## Two bugs the browser found, and no test could have

Both were in the drawing, and both looked right in code review.

1. **The mask was drawn transposed.** `rle_decode.js` returns COCO's order — flat index
   `row + column * height` — and `ImageData` is row-major. Copying the index straight across put
   every mask on its side: the object's 126×162 box contained a 162-pixel-wide smear that ran off
   the canvas and wrapped. The pixel count was still exactly `area`, so the page's own check passed
   while the picture was wrong. Fixed by mapping the index
   (`((index % height) * width + Math.floor(index / height)) * 4`), and pinned by a test.
2. **`scrollTop` was computed in the wrong pixels.** The figure the canvas sits in is capped, so a
   2509-pixel-tall row needs scrolling to the object — and `scrollTop` counts the pixels the canvas is
   *drawn* at, while the object's coordinates are the image's. Using the bitmap's scale is the same
   number only when the two scales happen to agree; here they were 1.0 and 1.525, so the object landed
   below the visible band of a blank grey box — which reads as a model that found nothing. Fixed by
   converting with `canvas.clientHeight / image.naturalHeight`, and pinned by a test.

The lesson worth keeping: **count pixels, do not look at screenshots.** A vision reading of the
transposed mask called it "thin pale-pink strokes"; a later reading of a *correct* mask that was
scrolled out of view called the canvas empty. Only the counts — 12,063 pink pixels inside
`[232, 924, 355, 1083]`, 1,152 blue ones for the box, `Σ mask = Σ area = 12,074` — said what was
true.

## How the rehearsal was done, and how to repeat it

A GPU is not needed to exercise the page, and no test of it exists in Python.

1. A stand-in container (scratch, not in the repository) answering the three protocol paths: `/health`
   with SAM 3.1's own declaration, both predict routes with one object built from a **real published
   mask** — `docs/scripts/fixtures/row-geometry.json`, `instance_row0` object 0, `area 12074` — and
   the blank 592×2509 image those coordinates belong to.
2. A one-row index written with the gateway's own `tests/conftest.write_index`, and `create_app`
   pointed at it with `models_path` naming the stand-in.
3. For the *pixel* checks, a throwaway proxy that serves the page, the API and the image from one
   origin: `data.global-wheat.com` sends no CORS header, so a cross-origin image taints the canvas
   and `getImageData` throws `SecurityError` — which is the design working, and useless for checking
   that a mask was drawn. Driving the real page through Chrome DevTools and reading the canvas back
   gave the numbers above.

The page was also driven by hand through the documentation site's frame; the connection, the
model list, the generated controls (`prompt` → text, `confidence_threshold` → slider at 0.5), the
dataset list, a run, and the response in `<details>` all behaved with no console errors.

## What the plan got wrong, and what is still open

- The plan's upload bullet said `fetch(url, {method: "POST", body: file})`. The gateway's public route
  declares `UploadFile`, so a raw body answers `400`; the page sends `FormData`, and the container —
  which does take raw bytes — receives them through the gateway. The plan now says so.
- The plan had "one module, one stylesheet beside it". Replaced, by the user, with `assets/`, one
  language to a file. `play.py` is now its docstring plus two substitutions.
- **Open:** the recorded call (the user's tuned parameters and result), the rehearsal against a real
  backend, and the second-model invariant check. The demo panel states in words that the record is not
  in the repository yet, and starts drawing the moment it is committed beside the page.
