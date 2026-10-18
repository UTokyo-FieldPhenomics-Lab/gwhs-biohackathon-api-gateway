# The gateway validates a model's parameters against the model's declaration

Plan: [`plans/2026101811_model-parameter-contract.md`](../plans/2026101811_model-parameter-contract.md).
This records P0 and P1 — the gateway half. P2 (the container, on labpc's 3090) is not done.

## What was wrong

`models.py` declared `prompt` and `confidence_threshold` in both predict signatures, while
`GET /models`, `/play`, and all three clients were already declaration-driven. The repository's own
rule said not to:

> nothing here may name a parameter: a `prompt` written into this package would be a second place the
> model's own contract lives. — `rules/gateway.md`

Consequences that were measured, not assumed: an unknown query parameter was forwarded without
inspection (so a typo reached a machine and came back as an opaque `400` from the container); a model
with any other parameter could not be documented at all, because `/docs` shows one signature for
every model; and `ModelParameter` had no `type`, so `{"type": "boxes"}` was silently dropped.

## What changed

- `ModelParameter`: `type` (`text`/`number`/`boolean`/`enum`/`boxes`/`points`), `values`, `enum`,
  `max_length`, `required`, `requires`, `excludes`, `above_maximum`. `default` widened to carry a
  boolean — before, `{"name": "recursive", "default": true}` parsed to `default=1.0` under Pydantic's
  lax coercion, which made `play.js`'s checkbox branch unreachable code.
- `ModelSummary.declared`: distinguishes "nothing has declared itself" from "declares no parameters".
  Without it, validation would refuse a working call in the one window where a backend answers
  `/health` with a body the gateway cannot parse (`_declare` returns early, so `alive` is true and
  `declared` is false — the query is forwarded, which is the correct behaviour).
- `ModelWarning` and `ModelPrediction.warnings`, relayed from the backend and never fabricated.
- `MAX_QUERY_BYTES = 2048` replacing `MAX_PROMPT_LENGTH = 200`: the reason for the old cap (the raw
  query goes to the pool) applies to the whole string, and with the names belonging to the model the
  gateway cannot know which one is long.
- `validate_query` in `models.py`, called between `registry.describe` and `registry.forward` in both
  routes by `_checked_query`, which also enforces the transport cap and returns the raw string for
  forwarding. Three layers: name, value, combination. A repeated name is refused with a message
  pointing at JSON, because both clients expand a list into repeated parameters (P5 will fix that).
- Both `Query(...)` declarations deleted; `Query` dropped from the imports.

## Numbers

- `uv run pytest`: **198 passed**. `test_models_surface.py` went from ~51 to 66 tests; fifteen are
  new, one per refusal (unknown name on both routes, out-of-bounds value, over-long text, no
  declaration, empty declaration, eight malformed geometry shapes, repeated name, a size below the
  measured list) and one per relay (a warning passes through, no warning is invented, a boolean
  default survives, a second model shape validates, the OpenAPI document no longer names a
  parameter).
- `uv run mypy`: clean, strict, 11 files. `uv run ruff check .`: clean.
- One pre-existing failure was fixed on the way through: `tests/test_app.py:349` had been 101
  characters since before this work started (`git show HEAD:` confirms it), so `ruff check .` was
  already red. A one-line reflow; nothing else in that file moved.

## What was decided while implementing

- **The clamp stays the container's act.** `above_maximum: clamp` means the gateway forwards a value
  above the ceiling unchanged; only the container knows its ceiling and the size it ran, so it is
  the one that reports. A test asserts the gateway does not add a warning of its own.
- **Geometry is checked for shape only.** Whether a coordinate is inside the image is not checked:
  `predict` could (the index carries `width`/`height`) but `predict_upload` cannot without Pillow,
  and the gateway keeps zero non-stdlib dependencies. An asymmetric check is worse than one
  authority, and the backend already decoded the image.
- **A label may be omitted** and then means positive — not a convenience invented here:
  `_init_box`/`_init_point` (`geometry_encoders.py:282-313`) default to `torch.ones`. So both
  `[[x, y, w, h]]` and `[[x, y, w, h, 1]]` are forwarded, and the shapes are tested.
- **`values` became floats** on the way through Pydantic (`[640.0, 768.0, …]`). Harmless — JSON
  numbers, and the page's `<select>` renders `String(640)` as `"640"` — and not worth a `list[int]`
  branch for a field that also has to accept a non-integer size.

## What is left

- **P2**: the container on labpc's 3090, against the updated image and **not through the pool** — a
  4090 or a 5090 answering instead changes the numbers this matrix turns on. Then the 12-row matrix,
  and the declaration is written from its results; a parameter that runs but does nothing does not
  ship.
- **P3**: the site's parameter table generated from the live `GET /models`.
- **P4**: the door — a second declaration shape is already exercised by
  `test_a_second_model_shape_validates_through_the_same_path`, which uses a YOLO-shaped fixture.
  No container and no page, as instructed.
- **P5**: the clients' value encoding. Until then, a client that passes a list still produces
  repeated parameters, and the gateway now refuses that with a message that says why.
