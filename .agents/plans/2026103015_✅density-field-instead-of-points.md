# Publish the counting model's field, not invented points

Continues [`2026102923_tasselnetv4-counting-model.md`](2026102923_tasselnetv4-counting-model.md), whose D1 and
D2 this reverses. Written 2026-10-30 because the session that agreed it is being compacted; read this
file, not the conversation.

## Why

The ten dots on a counting answer were never the model's output. TasselNetV4 is a local-count
regressor: its training loss is L1 between the predicted local-count map and the ground-truth dot map
summed into blocks, and it is evaluated by MAE/RMSE/WCA/R², with no instance labels, no set matching
and no localisation loss. The dots were `round(count)` attention cells chosen by the container, so an
over-count of fifteen objects shows up as fifteen dots scattered over empty ground — which is what a
participant saw on `salaamuko` (47 annotated, 61 predicted, official exemplars). The model's honest
output is the count and the field under it.

## Decisions

**D1 — a prediction carries the field.** `density` is
`{"size": [rows, columns], "values": [floats, row-major]}`, values are local counts rounded to three
decimals, and their sum is `count`. Raw rather than normalised, so a caller can add them up and get
the number back. The grid is the model's own: the density head answers 23, 21 or 17 cells per tile
depending on which of its three levels the exemplar's resized size selects (measured: below 32, below
64, below 128 resized pixels), while the attention map beside it is always 24×24 — so `size` travels
with the values and the crop reads the cell size off the grid instead of assuming 16. A square
picture therefore publishes 289–529 numbers, one cell is 16.7, 18.3 or 22.6 px of the 384-pixel-tall
frame, and that is 45–60 px on a 1024-pixel-tall photograph. The grid covers the frame the model saw (`resolution_used`), whose aspect
ratio equals the picture's, so a consumer can stretch it over the image. **The count is the sum of the
cropped field**, not of the raw tile output: the model puts a little mass on the zero padding, and
that mass belongs to no part of the photograph.

**D2 — `points` stays in the contract; this model stops sending it.** It was wrong for TasselNetV4,
not wrong as a field: a point-set regressor (P2PNet and its descendants) answers exactly
`[x, y, score]`, and the page already renders it. Deleting the field and the renderer would mean
rebuilding both the day one is added, so the field stays optional and the container simply does not
set it. Its docstring becomes model-agnostic — `score` is the model's own score, not a salience this
project defines — so nothing in the contract claims a local-count model places points. No exception
to `rules/project.md`'s additive-only rule is needed any more.

**D3 — the pixel layer keeps the name `masks`.** `field` was considered and rejected: in this domain
"field" reads as a field image, the photograph participants upload, not the layer drawn over it;
`masks` is the pixel layer above the picture, whatever a model put in it — class masks for a
segmenter, local counts for this model. `boxes`, `legend` and `watermark` are unchanged, and no fifth
toggle is added.

**D4 — the page draws the field as a smoothed heatmap inside that layer.** The grid is painted into an
offscreen canvas of its own size, then stretched over the picture with `imageSmoothingEnabled = true`,
because an unsmoothed 21-cell image over a 1024-pixel photograph reads as a mosaic rather than a
distribution. Alpha is the cell's value against the **field's own ninth decile**, with the cells above
it saturated: scaling by the peak leaves one blob, because a local-count field spans an order of
magnitude — measured on `gwfss` row 0, one cell held 1.9 and the rest 0.05–0.13 — while scaling by
nothing paints the whole frame. The colour is the one the dots used, alpha at the decile is 150/255,
and the deepest tint stops short of opaque so the photograph stays readable underneath.

**D5 — the fabrication stays deleted.** `infer.points_from_attention` is not kept in the container
even though `points` stays in the contract: taking the top `round(count)` attention cells is what made
an over-count look like detections. A point model emits its own points; the renderer and the wire
shape are the reusable half, and they are what this plan preserves.

## State of the working tree at 2026-10-30 15:xx — read before touching anything

A parallel session already started this and stopped mid-way. Verify each line before building on it.

- **`models/tasselnetv4/` (submodule, uncommitted):** `infer.count_and_attention` is now
  `count_and_field` and returns the stitched field with the left padding cropped (`_crop_padding`);
  `outputs.answer(model, count, density, warning)` builds `density` from it via `_field`;
  `serve.run_prompt` follows. **`tests/outputs.py` is now broken**: it still imports
  `points_from_attention` and asserts the old `CONTRACT_KEYS`, so `uv run tests/outputs.py` fails.
  `README.md` and the docstrings still describe points.
- **`gateway/` (parent, uncommitted):** `play.js`/`play.css`/`play.html` carry the box-handle, mode
  switch and preview-race work from earlier today (verified in a browser); `models.py` still declares
  `points` and no `density`; `docs/content/api/http.md` still describes points in its "Counting
  results" section; `rules/models.md` and `rules/gateway.md` likewise.
- **Running:** the `tasselnetv4` container on this workstation's `12713` was built from the committed
  code (`f0ef510`, points era); the demo gateway on `8123`; the three pool backends on the host serve
  the same points-era image.

## Progress at 2026-10-30 18:10 — finished, with one row left

S1–S5 are done. Container tests pass with no weights; the gateway's 307 tests, `mypy src`,
`ruff check`, `ruff format --check` and `zensical build --strict` are clean; `/play` was verified in a
browser on `gwfss` row 0 and then on the public API. `homepc-4090` and `lab-5090` were rebuilt and
restarted, the host pulled and restarted with its cache cleared, and a public answer carries `count`
and `density` with no `points`. **`lab-3090` is still the CPU container**, because this workstation's
NVIDIA module (610.57.04) and userspace library (615.71) disagree until it reboots — the one row not
done, and it changes nothing about the contract.

Details, measurements, the deploy record and the failure record are in
[`references/2026103016_density-field-instead-of-points.md`](../references/2026103016_density-field-instead-of-points.md).

## Steps

### S1 — finish the container

- `tests/outputs.py`: drop `points_from_attention`; assert `CONTRACT_KEYS` gains `density` and loses
  `points`, that `density.size` matches the field's shape, that `values` sums to `count` within
  rounding, that a count below zero publishes zero, and that `_crop_padding` drops exactly the padding
  columns.
- `README.md` and module docstrings: the field replaces the point selection; keep the
  `HEAD_CEILING`/`exemplar_clamped` paragraph.

**Acceptance:** `uv run tests/outputs.py` passes with no weights and no GPU; the image still builds.

### S2 — the contract

- `gateway/src/biohack_gateway/models.py`: add `class DensityMap` (`size: list[int]`,
  `values: list[float]`) and `density: DensityMap | None = None`; **keep** `points` and rewrite its
  Attributes entry as a point model's output rather than this model's salience.
- `gateway/tests/test_models_surface.py`: the counting answer carries `density` and its sum is the
  count; the existing points test stays as the point-model case; an unknown field is still dropped; a
  mask answer is unchanged; a body with `density` but no `objects` is still refused.

**Acceptance:** `uv run pytest`, `uv run mypy src`, `uv run ruff check .` and `ruff format --check .`
are clean; `GET /openapi.json` shows `density` on the prediction and still shows `points`.

### S3 — the play page

- `paint()`: when `answer.density` is present, paint it into the mask layer — an offscreen canvas at
  `size`, `imageSmoothingEnabled`, alpha by `value / max`, the colour the dots already use. When `rle`
  objects are present the existing mask painting stands; the two never meet in one answer today.
- Nothing else in the view changes: `masks`, `boxes`, `legend` and `watermark` keep their names, and
  the point renderer and `last.points` stay as the point-model path.
- After review the row gained a fifth eye, `image`, placed first, and `boxes` became the toggle for
  every rectangle on the picture — see the reference's "display row after the second round".

**Acceptance:** on `gwfss` row 4 and on a PAC-Somalia image, the field covers the objects and the
`masks` toggle hides it; a segmenter still paints masks; `node --check play.js` passes.

### S4 — documentation

- `docs/content/api/http.md`: rewrite "Counting results" as count plus field — what the grid is, that
  the sum is the count, that it is not a segmentation, and how coarse it is — and say in one line that
  a point-counting model answers `points` instead.
- `rules/models.md` and `rules/gateway.md`: replace the sentences that name points as this model's
  output.
- Model page and `fixtures/model-directory.json`: unchanged by this step (see Open).

**Acceptance:** `uv run --project docs zensical build --strict` is clean.

### S5 — verify and publish

- Rebuild the image; run one PAC-Somalia image and `gwfss` row 4 through the container directly.
- Ship the same 31 MB tarball to `homepc-4090` and `lab-5090`, rebuild, restart both with
  `BACKEND_TOKEN` and `TNV4_MACHINE` as before.
- On `gwhs-host`: `git pull --ff-only`, then `sudo systemctl restart biohack-gateway`;
  `~/.config/biohack-gateway/models.json` already lists `tasselnetv4` at three addresses.
- Clear the host cache once: an answer stored before this change has no `density`.
- Public check: `POST /models/tasselnetv4/predict` carries `count` and `density`, and no `points` —
  the field is in the schema, this container sets none.

**Acceptance:** `GET /models` reports `backends_up: 3`; one public answer carries `count` and
`density`.

## Traps this repository has already paid for

- **The gateway reads `/play` at import.** Editing an asset under `gateway/src/.../assets` changes
  nothing until the process restarts — the demo gateway on `8123` included. A browser showing an old
  page looks exactly like a bug in the page.
- **`curl` treats `[...]` in a URL as a range.** Pass `-g`, or percent-encode the `boxes` parameter.
- **A background build inside WSL 2 dies with the SSH session.** Run it in the foreground of a long
  ssh, or keep the distro alive; the 5090 needs `DOCKER_CONFIG=/tmp/dfcfg` and `wsl -e bash -lc`.
- **The demo gateway's token pool** is `/tmp/opencode/demo/tokens.json` (`demo`, plus `dev` = the
  `TEST_API_TOKEN` from `.env`); its index has rows 0–9 of `gwfss` and `gwhd`; it uses
  `BIOHACK_BACKEND_TOKEN` from the environment.
- **A container is not the surface.** The host runs from git: a change that is not pushed and pulled
  leaves the public API answering the old shape while the container answers the new one — that is
  exactly how `count` was dropped on 2026-10-29.

## Open, not in this plan

- The docs model page for `tasselnetv4` (needs an authenticated `GET /models` and
  `make_models.py`; the fixture refresh belongs with it).
- PAC-Somalia presets on `/play`: `yicib` 56→54.9, `talal` 42→45.3, `mir_qalooc` 69→68.9 are the
  images this model answers well on; `salaamuko` over-counts by about 15 and is a poor demo.
- A per-class accuracy note on the model page: aggregate MAE on the official test set is 8.75 on
  `salaamuko` (48 images) and 10.8 over 32 sampled species, against the paper's 8.88 (3-shot).
