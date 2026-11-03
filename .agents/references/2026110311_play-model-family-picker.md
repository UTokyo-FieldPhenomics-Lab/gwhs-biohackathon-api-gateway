# The model picker's checkpoint step

**2026-11-03.** `/play` offered six ids in one dropdown: `dinov3-vitl16`, `dinov3-vith16plus`,
`gwfss-segformer-b4`, `internvl-u`, `sam3.1-meta`, `tasselnetv4`. The two DINOv3 ids are one encoder
at two checkpoints, and the flat list made a checkpoint look like an unrelated model. The docs had
already merged their pages; the picker had not.

## The decision

Two controls: the first names a family or a plain model, the second names the checkpoint. The family
is page state only. `selectedModel()` is the one reader of the pair, and every path that calls a
model — the sample curl, the run, the 10 s directory poll — names the id. The API is unchanged:
`dinov3-vitl16` and `dinov3-vith16plus` remain two ids, two declarations and two ports.

A family is declared in `MODEL_PRESETS` (`family`, `label`, `order`), the one table the page may name
a model in. Only a family with two served ids becomes a family option; a single served checkpoint
stays its id, because a second control with one choice decides nothing. The family value is
`family:<name>`, which no model answers; the page test keeps that value out of every request path.

Alternative considered: an `optgroup` in one select. It needs no new state and keeps the id visible
when closed. Rejected because the reader asked for the two-step choice and because a second control
can show the checkpoint's own label. The comparison was rendered and answered on a page.

## What changed

- `play.html`: `#variant`, hidden, after `#model`, named “checkpoint” for assistive technology.
- `play.js`: `buildModelChoices`, `syncVariant`, `selectedModel`, `selectModel`; `connect` resolves
  `?model=<id>` into both controls; `startOver` clears both; the run and poll read the pair.
- `loadModel` now has a per-call token: a declaration that arrives after the pair changed is dropped,
  instead of leaving one id's parameters under another id's name.
- `clearAnswer` drops the answer while keeping the photograph and the reader's boxes; `loadChosen`
  calls it on every model change, and `preview` calls it for an image change, so a text answer no
  longer survives one either. A run that outlives a model change is tagged with `modelEpoch` and
  discarded instead of drawn under the new id.
- Tests: the preset invariant now names both DINOv3 ids; one test holds the second control to the
  id-on-the-wire rule, and one holds the model change to clearing the previous answer.
- `models.json`, the API and the docs links are unchanged.

## The answer a model change used to leave behind

The picker exposed an older gap. `loadModel` deliberately left the picture and its answer alone, so
after a run, changing the model left the previous model's masks, boxes, legend, count, response and
machine on screen under the new model's name. `clearAnswer` redraws the photograph from the image
the answer arrived with, clears the answer's DOM, response and machine, keeps the reader's boxes and
the address, and lets `preview` restore the image caption. The clear is synchronous, so no frame
shows a mask whose model is no longer selected.

`preview` clears too, which is what makes a dataset change clear a text answer; before, the answer
panel was only cleared when the viewer was emptied. A run already at a backend cannot be recalled,
so `modelEpoch` makes its answer land as a discarded answer rather than a wrong one.

## Verification

- `uv run pytest -o addopts="" -q` in `gateway/`: **317 passed**.
- `mypy src`: clean; `ruff check src tests/test_app.py`: clean; `node --check` on `play.js`: clean.
- Local gateway (`create_app` with a one-row `gwfss` index and six dead backends), Firefox:
  - `?model=dinov3-vith16plus` → first control `DINOv3`, second `ViT-H+/16`, curl names
    `/models/dinov3-vith16plus/predict`.
  - Switching the checkpoint to L rewrites the curl to `/models/dinov3-vitl16/predict`.
  - Selecting `gwfss-segformer-b4` hides the second control (`display: none`, width 0).
  - Re-selecting `DINOv3` lands on L (`order: 1`), not H+.
  - `/play` with no query lands on `gwfss-segformer-b4` with no second control.
  - Forget clears both and hides the second control.
  - With only `dinov3-vitl16` served, the same URL lists it as a plain id and hides the second
    control: a family of one is not a family.
- The clearing, on a second local gateway whose backends answer (`FakeBackends`, one object and no
  mask decoder): run `sam3.1-meta` on `gwfss` row 0, then select `internvl-u` → marks 0, response
  hidden, machine empty, legend cleared, answer panel hidden, canvas still the 512 × 512 photograph,
  caption `gwfss row 0 · 512×512`.
- The local page logs a module-MIME error for `/clients/global-wheat.js` because the development app
  does not proxy the docs client; production serves it from the docs origin. Unrelated to the picker.

## Deploy

The page is served by the gateway, not the docs site: the host needs this commit and a service
restart. The docs site needs no change.

Deployed 2026-11-03: `gwhs-host` pulled to `772e4f6` and `biohack-gateway` restarted; the live
`/play` serves `id="variant"` and the two `family: "DINOv3"` presets. The docs page frames the API,
so it shows the new picker without a rebuild. The local page test used a throwaway gateway with a
one-row `gwfss` index and six dead backends; the live pool was not touched. The clearing fix was
then pulled and restarted as `833adcc`; the live page carries `clearAnswer` and the `modelEpoch`
check.
