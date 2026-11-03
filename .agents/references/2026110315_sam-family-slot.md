# SAM 3.1 as a family: `original` now, `finetuned` when a container answers

On 2026-11-03 the play page gained a two-step model picker (`2026110311_play-model-family-picker.md`).
This note records putting `sam3.1-meta` and a wheat fine-tune into it as one family, why the second
member is visible with nothing behind it, and what still has to be written when the checkpoint ships.

## What the picker actually needs

The choice is derived, not declared twice:

- `buildModelChoices` walks `GET /models` and groups entries by `presetFor(id).family`, a label in
  the page's own `MODEL_PRESETS` table;
- a family with fewer than two **listed** ids stays a plain id, so the family appears the moment the
  second id is in the directory;
- what is sent is always an id, never `family:…` (`selectedModel`), so the wire contract is unchanged;
- Run is disabled from `!declaration.backends_up`, which already existed for a switched-off machine.

So the only code that had to name anything is the preset table — the one place the page is allowed to
mention a model id.

## What changed

- `gateway/src/biohack_gateway/assets/play.js`: `sam3.1-meta` becomes `family: "SAM 3.1"`,
  `label: "original"`, `order: 1`; a new preset `sam3.1-wheat`, `label: "finetuned"`, `order: 2`.
  The id follows the decision recorded in `../plans/2026101617_model-api-surface.md`: a collaborator's
  fine-tune is named after its training data, not `-ft`.
- `gateway/tests/test_app.py`: the family test also counts `family: "SAM 3.1"` twice.
- `gateway/tests/test_models_surface.py`: a new check that an id mapped to `[]` is listed with
  `backends_up: 0`, nothing declared, and answers `503` without asking any machine.
- `deploy/gpu-backends-local.md` §4: one paragraph on the empty list as a held place, so an operator
  does not delete the line as a mistake.

## Why the second member is empty and visible

Host configuration holds the decision about what is served, not the page:

    {"sam3.1-meta": ["http://<address>:12711"],
     "sam3.1-wheat": []}

`load_models` accepts an empty list, so the id is in `GET /models` with no declaration and no live
backend. The picker then shows `SAM 3.1 · original / finetuned`, and choosing `finetuned` says
**"no backend is answering, so nothing can be run"** with Run disabled. That is the truthful state
rather than a hidden one or a second, invented id.

Verified end to end on 2026-11-03 against a gateway built from this checkout, with `sam3.1-meta`
pointing at the container on `127.0.0.1:12711` and `sam3.1-wheat` empty: the first dropdown read
`SAM 3.1`, the second `original` and `finetuned`, and the pool row named the missing machine.

## What is still open

- **No backend, no declaration.** Nothing serves `sam3.1-wheat`, so it declares no parameters and the
  play page offers no controls for it. The port it will take is the next free one, recorded in
  `../rules/backend-pool.md` when a container runs — not assigned here.
- **The documentation page is not a family page.** `docs/content/models/sam3.1-meta.md` is one page
  for one id. When the fine-tune answers, it becomes `<!-- models: sam3.1-meta sam3.1-wheat -->` with
  one `parameters:<id>` block each, per `../rules/docs.md`, and `docs/scripts/fixtures/model-directory.json`
  is refreshed from the host — the table cannot be written from a plan.
- **No example for the fine-tune.** `examples` in the presets is a demonstrated call; there is nothing
  to demonstrate until the checkpoint answers, and an example without one would fail in public.
- **The plan's trigger stands.** `rules/models.md` still says a fine-tune is where field counts come
  from; this change prepares the surface for it and does not claim the count.
