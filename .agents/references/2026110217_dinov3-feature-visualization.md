# DINOv3 features in the page and on the site

2026-11-02. Branch `main`. Worked in `clients/`, `gateway/src/biohack_gateway/assets/`, `docs/`, and
the pool inventory. The containers themselves were the previous session's
([`2026110215_dinov3-backbones.md`](2026110215_dinov3-backbones.md)); this one makes their answer
visible and explains it.

## What was added

- **Client decoders.** `decode_features(field)` in Python, JavaScript and Java: base64 of
  little-endian float16, checked against `rows × columns × dim × 2`, returned as a `(rows, columns,
  dim)` float32 array (Python), one flat `Float32Array` (JavaScript), or a `GlobalWheat.Features`
  with `float[rows][columns][dim]` (Java). `Client.predict` and `predict_file` attach
  `features.grid` beside the raw `data`; `decode=False` / `decode: false` leaves the body as written,
  as it does for a mask and a counting field. Java always shapes, as it always does.
- **`/play`'s features layer.** One eye, shown only on the two backbone ids, draws the first three
  principal components of the patch grid over the photograph at 55% (full opacity with the image eye
  off). The caption names the grid, its float16 size, and that the colours are this picture's own.
- **The computation is power iteration, not a covariance.** A 32 × 32 × 1024 grid is 1 048 576
  numbers; forming a 1024 × 1024 covariance is a billion multiply-adds, while one iteration is two
  passes over the patches. Three components, ten iterations, a fixed seed and a sum-fixed sign; the
  whole projection measured **178 ms** for 32 × 32 × 1024 in Node, and the page does it once per
  answer. The docs figure uses NumPy's SVD offline and matched the page's picture.
- **Documents.** Two model pages (`dinov3-vitl16.md`, `dinov3-vith16plus.md`), the models index
  (five models, three tasks and two backbones), a two-panel figure of `gwfss` row 4, a
  "Backbone results" section in `api/http.md`, and a `features.grid` paragraph in `api/clients.md`.
  The pages say what the output **is** — last hidden state, `[CLS]` + four registers + patch tokens,
  patches row-major at 16 px, no softmax, no class, no mask — and what can be built on it: patch and
  region cosine, kNN segmentation, a linear probe or fine-tune, CLS retrieval/clustering, change
  comparison. The live similarity query is described as a recipe, not a page control.
- **`side` is stepped by one.** A bounded numeric whose default and bounds are integers gets
  `step="1"` and a whole-number readout; a confidence threshold keeps `0.01`. The page had been
  stepping 224–768 by 0.01, which made every arrow key a no-op and printed `512.00` in a field that
  the container rounds anyway.
- **The response panel elides the payload.** `features.data` prints as
  `"… 2 796 204 base64 characters"` and `embedding` as `["… 1024 floats"]`; the body is otherwise
  complete. Before this, opening the panel put a 2.6 MB text node in the page.
- **A landing model that is not the alphabetically first.** The directory is in id order, so a
  backbone sorted first and the page opened on a grid to decode. `VIEW_BY_MODEL` now carries a
  `landing: true` on `gwfss-segformer-b4`, and the connect path reads the flag. The gateway test that
  allows a model id in exactly one place still passes.

## What broke, and what to do differently

- **`[hidden]` lost to `display: inline-flex`.** The `edit` / `locked` switch was visible on every
  model, including the ones that declare no `boxes`: `#boxes-mode { display: inline-flex }` beat the
  user agent's `[hidden] { display: none }`. The same would have happened to the new features eye.
  Both now have an explicit `[hidden] { display: none }` rule. This is the bug a reader reported as
  "the switch is for bbox input, not a general component" — the switch's JavaScript was already
  scoped to `boxes`; the CSS was not.
- **The gateway tests pin the page's exact defaults.** `test_the_model_page_has_one_view_toggle_per_layer`
  compares the literal `VIEW_BASE`, and `test_the_model_page_names_no_model_and_no_parameter` counts
  the one allowed model id. Adding a layer means updating the first; a landing default means putting
  the id in the view table and nowhere else. A literal id in the connect path would fail the second.
- **The docs generator had no clamp sentence.** A bounded numeric with `above_maximum: clamp` printed
  only `224 – 768`, hiding that a larger value is answered at the ceiling. `_accepts` now appends the
  sentence; `imgsz` already said it through `values`.
- **The recorded fixture is a moving target.** `docs/scripts/fixtures/model-directory.json` includes
  `backends_up`, `machines` and `checked_seconds_ago`, so refreshing it changes one long line even
  when no declaration did. That is expected; the tables are what the model-page diff check reads.

## Deployment order

The page loads `GlobalWheat` from the documentation site, not from this host. **Publish the client
before the gateway:** push `clients/**`, wait for the docs workflow, confirm
`https://docs.global-wheat.com/clients/global-wheat.js` contains `decode_features`, then pull and
restart the gateway. A page deployed first shows "the client does not decode features" and the
photograph; nothing else breaks.

## Measured

- PCA in the browser: 178 ms at 32 × 32 × 1024 (Node, the page's own function); a 48 × 48 grid is
  about twice that and still not visible as a pause.
- The page's end-to-end call is the previous session's: 2.3 s cold through the gateway (including the
  CDN image) and 20 ms on a cache hit; the feature layer adds the decode and the 178 ms projection.
- The answer at `side=512`: 32 × 32 × 1024, 2 097 152 bytes of float16, 2.8 MB as JSON, 6.3 MB at
  the 768 ceiling.

## Deferred, with the trigger that would bring it back

- **Click or drag-to-query similarity in the page.** The wire and the client already carry the grid;
  adding it is a reference layer, a click-or-drag branch and a red ramp (red, not yellow/green: wheat
  is yellow). The trigger is a participant asking to select a region in the page, or a demo that
  needs the query; the docs page shows the recipe meanwhile.
- **`predict_file` resizing to a backbone's `side`.** The client's upload target follows `imgsz` and
  falls back to `resolution`; a caller who passes `side=768` to the L gets a 512-pixel upload that
  the container upscales. The common case (the default side) is correct. Trigger: a participant
  reports a soft upload at a non-default side, or the clients are touched again for another reason.
- **The 7B checkpoint.** Still out: 25 GiB, one card to itself, 4096 dimensions.

## Open

- **`5947970` still carries a private address in public history.** The alias fix is `2c3e247`; the
  decision to rewrite or accept is the repository owner's, and no later session should make it
  silently.
- **`backend-pool.md` says `lab-3090` serves all five and the ports 12714/12715 are assigned.** The
  other machines do not run the backbones; a machine that does must add the two rules, not reuse
  another model's port.
