# A counting model on the model surface — TasselNetV4

> **Partly superseded on 2026-10-30.** D1 and D2 publish points; the model's answer is a count and
> a local-count field, and points were our invention. See
> [`2026103015_✅density-field-instead-of-points.md`](2026103015_✅density-field-instead-of-points.md).

## Goal

TasselNetV4 answers a **count** for a published row in the same prediction body the mask models use:
the count, and the points its own visualization places. The gateway validates and relays those two
fields instead of dropping them, `/play` shows the count and draws the points, and the container
lives in `models/tasselnetv4/` beside the two that already answer there.

What blocks it today is one shape. `ModelPrediction` carries `objects` — bbox, score, area, COCO
RLE — and `_prediction` re-validates every backend body against it, so a container's `count` is
discarded before a caller ever sees it.

## What was measured before this plan

- Upstream code: `tiny-smart/tasselnetv4` at `c0bc80d5c51a57662238267deb1576acf4a62add`
  (2025-12-08), Apache-2.0, 31 MB, no weight in history.
- The model builds from the repository's own `cfg_test` with torch 2.15 + `timm==0.5.4` on Python
  3.13 and forwards on CPU: 94.7 M parameters, and the answer is a density map whose sum is the
  count (17×17 per 384 tile) plus an attention map (24×24). No `flash_attn` or `xformers` import,
  so the pinned CUDA 11.8 environment is not needed.
- Weights: `akaxiao/TasselNetV4` on Hugging Face is public and ungated, card licence Apache-2.0;
  `tasselnetv4.pth` is 1,128,783,566 B with LFS oid `ef07c8ed…`. The downloaded file matches both,
  and `load_state_dict` into the config-derived model reports **0 missing, 0 unexpected** tensors —
  the shipped code and the shipped checkpoint are one build.
- Two defects in the shipped code, both of which stay out of `vendor/`: `cfg_test/…yml` carries
  `xenhance`, which the constructor does not accept, so `test_checkpoint.py` fails as published;
  and `forward` reads `samples[3][0]`, so `size` has to be a one-element tensor.
- The upstream Drive link for the checkpoint now answers "sign in required". The HF copy is the
  fetchable identity, and it is the same fetch-by-revision pattern `gwfss_segformer` already uses.

## Decisions

**D1 — Two additive fields, and nothing else on the wire.** `ModelPrediction` gains
`count: float | None = None` and `points: list[list[float]] | None = None`. Optional and unset-free
by default, so every mask answer is byte-identical to before and the cache, the lane policy and the
upload route are untouched. A density grid is **not** published: it is an intermediate of the
model's visualization, its resolution follows the tiling, and it has no unit a participant can use.

**D2 — A point is `[x, y, score]` in the original image's pixels.** `score` is that cell's attention
weight divided by the maximum of the same answer, in [0, 1]. It is **relative salience, used for
ordering** — not a calibrated confidence, not comparable across images or models, and the model
page must say so. Points are quantized to the attention grid, 16 px per cell in tile space, which
`resolution_used = 384` makes legible.

**D3 — `objects` stays required.** A counting container answers `objects: []`. That keeps one body
shape for every model, keeps three clients guessing nothing, and avoids a schema change that would
read as a breaking one to a participant who already parses `objects`.

**D4 — The prompt is `boxes`, and it is the model's own parameter.** TasselNetV4 is class-agnostic:
it cannot know what a wheat head looks like, so the request carries exemplar boxes and it
extracts-and-matches their appearance across the image. The wire form is the one SAM 3.1 already
uses, `[[x, y, w, h, label]]` XYWH in the image's pixels, and this container accepts `label = 1`
only — extract-and-match has no negative exemplar, so a `0` is a `400` rather than a silently
ignored box. `boxes` is declared `required: true`, so a missing prompt is refused by the gateway's
existing validation rather than by the container. One to N exemplars all run; three is what the
paper reports and what the page hints at.

**D5 — The container declares, the gateway relays, the page renders by presence.** No new
declaration field and no branch on model id: `/play` draws a count when the answer has one, points
when it has points, objects when it has objects. One shape means the same page serves a counting
model and a segmenter.

**D6 — The box editor is generic, not counting-only.** The crosshair, the rubber band and the
editable `boxes` input appear whenever the declaration names a `boxes` parameter, so SAM 3.1 gains
the same editor. Editing the text updates the overlay live; an unparsable value keeps the last valid
overlay and says so; a drag under 4 px is treated as a click, not as a box; Esc and the right button
cancel.

**D7 — Weight identity comes from the platform, not from us.** `Dockerfile` fetches
`tasselnetv4.pth` from `akaxiao/TasselNetV4` at a revision, asserting the byte count and the LFS
oid, as `gwfss_segformer` does. No local hash file, no mirror into the shared store: the repository
is public and the file is not a credential.

**D8 — Host port 12713, inner port 12711.** `12711` is `sam3.1-meta` and `12712` is
`gwfss-segformer-b4`, and the AWS security group already opens `12711`–`12713`. The container listens
on `12711` like its siblings; only the host-side mapping carries the model's number.

## Steps

### P1 — The contract (first, because it is what drops the field) — **done**

- `gateway/src/biohack_gateway/models.py`: `count` and `points` on `ModelPrediction`, docstrings in
  the file's own voice.
- Tests: a counting answer round-trips; a mask answer is unchanged field for field; an unknown field
  is still dropped; a `count` with no `objects` key is refused, which is what keeps D3 honest.

**Acceptance:** the new tests fail before the field and pass after; the whole gateway suite is green;
`GET /openapi.json` shows both fields as nullable and not required.

### P2 — `/play` — **done**

- The box editor of D6, in `play.py`'s assets: crosshair over the image, drag to draw, second click
  or release to finish, Esc and right button to cancel, the input kept in step in both directions.
- Count badge and point rendering, both driven by field presence.
- The generated `curl` changes only through the `boxes` value it already interpolates.

**Acceptance:** with the counting container up, a row plus three drawn boxes yields a count and
points; editing the input's numbers moves the drawn boxes without a request; a mask model still
draws its objects and never shows a count badge.

### P3 — Docs — **done, except the model page**

- `docs/content/api/` gains a short section on the counting shape: what `count` is, what a point is,
  what `score` is not, and where the prompt comes from.
- `clients.md` field list gains the two names; the decoder sections stay as they are, because a
  point needs no decoder.
- `fixtures/model-directory.json` is refreshed from an authenticated `GET /models` with
  `make_models.py`, never hand-edited.

**Acceptance:** the site builds `--locked --strict`; the model page lists the container's declared
parameters and its card; no page tells a participant to send a box of four numbers.

### P4 — The container, `models/tasselnetv4/` — **done, built and answering on a 3090**

Laid out like `sam31_official/`, per `models/README.md`:

| part | owns |
|---|---|
| `vendor/` | the upstream tree at `c0bc80d`, unchanged, `UPSTREAM_COMMIT` beside it |
| `serve.py` | `/health` declaration — `boxes` required, `resolution: 384`, `computed_by`; the two predict routes and the fetch guard |
| `outputs.py` | count, points and `objects: []`, with the point selection of D2 in one place |
| `infer.py` | tiling 384 with the upstream padding and overlap handling, exemplar crops from the drawn boxes, density sum, top-k points, `--report-vram` |
| `Dockerfile`, `pyproject.toml`, `uv.lock` | CUDA runtime + `uv` + torch; the checkpoint fetched at build time per D7 |
| `tests/outputs.py` | the answer decodes and the point count matches the count — no weights, no GPU |
| `README.md` | the folder's own decisions, as the sibling READMEs have |

**Acceptance:** `tests/outputs.py` passes with no weights; `infer.py` on a GWHD row gives a count
within a few heads of the visible number and a point per counted head; `docker build` succeeds on a
machine that has never seen the file; `/health` answers without a token.

### P5 — Registration and the first real call — **the call was made locally; the host entry is not written**

- `models.json` on the gateway host gains the entry; `.agents/rules/backend-pool.md` gains the
  `12713` row with a dated observation once the container answers.
- One authenticated call through the gateway, then the same row through `/play`.

**Acceptance:** the gateway's `/models` lists it, the answer carries `count` and `points`, and a
second identical call is served from the cache with `cached: true`.

## Where it stands

Landed: the two fields and their tests (305 gateway tests green), the generic box editor and the
count/point rendering, the docs section, `models/tasselnetv4/` built into a 19.9 GB image that
answers `/health` and a real GWHD row on the 3090 (`count` 30.83, 31 points, 0.2 s warm), and the two
rules lines. Deviations from the plan are recorded in
[`references/2026102923_tasselnetv4-counting-contract.md`](../references/2026102923_tasselnetv4-counting-contract.md):
the inner port is 12711 rather than 12713, the checkpoint comes from Hugging Face at
`0f7ed8cd…`, the image is built with `torch==2.10.0` (there is no 2.15), and a third upstream defect
— a density head with no level above 128 px — turned into a `400`.

VRAM is now measured rather than bounded — 0.79 GB resident, 0.07 GB per request, unchanged from a
1024 px row to a 49-megapixel image — and the labels and `serve.py` defaults carry the rounded figures
(0.8 + 0.1 GB), so the start-up check asks for 0.9 GB instead of the first build's 2.5 GB.

Open: the host's `models.json` entry and restart, and the model page generated from an authenticated
`GET /models`.

## What is not in this plan

- **Counts on the dataset surface.** No count-of-record exists in `gwhd`; the model's number is an
  answer, not ground truth.
- **A density grid on the wire.** D1 says why; the trigger to revisit is a participant asking for
  the spatial distribution and needing more than points.
- **Ground-truth exemplars in `/play`.** Decided against: the participant's own boxes are the point
  of a class-agnostic model.
- **A text prompt.** The model has none; `prompt` stays the mask models' parameter.
- **A second checkpoint or a fine-tuned build.** One id, one container, per `rules/models.md`.
- **Publishing the container image anywhere.** Backends only, as before.

## Risks

- **The points are not the model's native output.** They come from its visualization path — top-k
  attention cells, `k = round(count)` — which upstream applies with extra knobs (a dilation, a
  Gaussian). Our `outputs.py` fixes one formulation and the docs say it is a visualization choice,
  not a measurement. If it proves misleading, the fallback is count-only, which D1 already allows.
- **Tiling is the container's own logic.** A 1024×1024 row becomes 3×3 tiles with padding and an
  overlap at the seams; a seam that double-counts is invisible in a sum and obvious in the points.
  The check in P4 compares the point count against the count, not against the image.
- **A count is a weak reproducibility story.** Two machines will disagree by a head or two and there
  is no mask to compare; `computed_by` is what makes the difference legible.
- **`/play` sends what a participant drew.** A box drawn off the object drags the count with it, and
  that is the model working as defined rather than failing — the page must not present the count as
  an independent judgement of the image.
