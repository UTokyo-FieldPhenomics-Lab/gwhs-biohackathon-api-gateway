# Model surface: the parameters are the model's — the SAM 3.1 migration

## Goal

SAM 3.1's own per-inference parameters — text, boxes, points, confidence threshold, image size — can
be passed as flat query parameters, are published with their bounds, are validated once in the
gateway, and are forwarded verbatim. Nothing about the wire changes for a caller who already passes
`prompt`; what changes is that the surface stops pretending those two names are the only ones.

**Scope: SAM 3.1 only.** YOLO is the reason the mechanism is generic, not work in this plan: the
declaration, the validation, and the two client helpers are written model-blind, and P4 is the
door — deferred, with one test that keeps it open.

The starting point is a rule this repository already states and its code already breaks:

> A model's control parameters are the model's, never the gateway's … it is why nothing here may
> name a parameter: a `prompt` written into this package would be a second place the model's own
> contract lives. — `.agents/rules/gateway.md`

`models.py:1301-1311` and `models.py:1368-1378` name exactly that parameter. `GET /models` is
declaration-driven, the `/play` page is declaration-driven (`play.js:controlFor`), and all three
clients already take arbitrary parameters (`**params`, `...params`, `Map<String, ?>`). The gateway's
FastAPI signature is the only place that is not.

## Decisions

**D1 — The wire, unchanged but now honest.** Path names the model (the only mandatory name), the
query carries that model's parameters, the body carries the operand.

```
POST /models/{model}/predict?<the model's own params>          body {"dataset": str, "row": int}
POST /models/{model}/predict/upload?<the model's own params>   body: the image bytes, as multipart
```

Flat parameters only. No `?params={...}` envelope: it makes `curl` unreadable, defeats
`URLSearchParams` and Swagger, and collapses per-parameter validation into one opaque blob. POST with
a query string rather than parameters in the body because the body is already spoken for.

**D2 — No cross-model normalisation. The names are the official ones.** SAM 3.1 declares `prompt`
(`set_text_prompt(prompt=)`), `boxes` and `points` (`add_geometric_prompt(box=, label=)` and the
`Prompt` sequence those append to), `confidence_threshold` (`Sam3Processor(confidence_threshold=)`)
and `imgsz` (the processor's `resolution`). A wrapper, not a dialect.

**D3 — Structured values are JSON in one parameter, in the image's own pixels.**

```
boxes=[[x, y, w, h, label], ...]      # XYWH, top-left origin, pixels
points=[[x, y, label], ...]           # xy, pixels
```

`label` is `1` (positive) or `0` (negative) and may be omitted, meaning positive — the model's own
default, `_init_box`/`_init_point` in `geometry_encoders.py:282-313` assume all-positive when no label
is given. Order is preserved: the official example adds positive boxes before negative ones. The
label space is two, because `encode_boxes_as_points=False` in `model_builder.py:285`; the six-label
variant that turns box corners into typed points is not the configuration these weights run, so no
corner semantics leak into the API.

Pixels, not fractions, because that is the official image example's caller-facing format
(`box_input_xywh = [480.0, 290.0, 110.0, 360.0]`), it is the same convention as the published
`bbox`, and a participant's own detector already speaks it. The container does the two conversions
the notebook does: `box_xywh_to_cxcywh` + `normalize_bbox` for boxes, `x/width, y/height` for points
— the encoder's contract is "boxes in normalized CxCyWH, points in normalized xy"
(`geometry_encoders.py:474`).

Only a **structured value** is JSON, never the parameter set: `prompt="wheat head"` stays a plain
string and the call stays `predict(model, dataset, row, prompt="wheat head")`. One value per name is
also what the clients must encode — repetition of a name is a dataset-filter convention
(`?tags=a&tags=b`) and must not leak into the model surface, where `boxes` would be split into one
parameter per number.

**D4 — Only parameters that cannot raise the admitted memory peak may be exposed.** `device` and
`half` are deployment properties and never enter the contract. `imgsz` passes this test in one
direction only — downwards — which is why it is exposed with a ceiling and a clamp (D11).

**D5 — Three layers of interception, before forwarding.**
1. **Name**: a parameter the model did not declare, including a typo (`?promt=`) and including an
   official parameter we chose not to expose (`?device=`), is `400` `invalid_request`; the message
   lists what the model does accept, so an agent stops guessing after one refusal.
2. **Value**: `type` parse, `enum`/`values` membership, `minimum`, `maximum` — `400` otherwise,
   except where the declaration names a policy instead: `above_maximum: clamp` accepts anything above
   the ceiling and leaves the clamp to the container (D11).
3. **Combination**: `requires` and `excludes`, plain name lists; and per element shape — `boxes`
   elements are arrays of four or five numbers with the label in `{0, 1}`, `points` elements arrays of
   two or three.

After validation the query is forwarded **verbatim** (`request.url.query`). The backend remains the
authority on semantics; the gateway only refuses what the declaration itself already answers.

For SAM 3.1 no combination rule is needed and none is declared: `_encode_prompt`
(`sam3_image.py:168-211`) concatenates the text features and the geometry features into one prompt
tensor, so text, boxes and points are combined by construction, not by luck. That settles the earlier
open question — what remains unmeasured is *quality*, and P2's matrix is how it gets measured.

**D6 — Validation is skipped only when the model has no declaration at all.** `parameters: []` is
ambiguous today: it means both "no backend has ever answered" and "this model takes no parameters".
Validating against the first would reject a working call the moment a container restarts, since the
gateway learns declarations at startup. Add `declared: bool` to `ModelSummary`; skip validation only
when it is `False`, and log once. A declared model with an empty parameter list still rejects every
parameter. This is the one place the change can break a working call, and the flag is what closes it.

**D7 — The response body is fixed and shared.** Parameters follow the official API; the response
keeps the shared COCO object shape (`bbox`, `score?`, `category?`, `rle?`) because `gateway.md`
requires one geometry across both surfaces, and a participant writes one decoder. The container
converts, not the caller. `category` is present only when the model was given a text prompt: a
geometry-only answer has no label, and publishing the container's dummy `"visual"` concept as one
would be a lie.

**D8 — One transport cap replaces one per-parameter cap.** `MAX_PROMPT_LENGTH = 200` goes; the
reason it existed (the raw query is forwarded to the pool) applies to the whole string, so
`MAX_QUERY_BYTES = 2048` covers any number of parameters. A hand-written box list is about 30 bytes
an entry.

**D9 — The gateway checks shape, not coordinates.** `predict` could check a point against the row's
`width`/`height` (the index carries them), but `predict_upload` cannot without Pillow, and the gateway
keeps zero non-stdlib dependencies. An asymmetric check is worse than one authority, so coordinates
stay the backend's business — it already decodes the image.

**D10 — Points are wired through the container's own subclass, not through the vendored API's gap.**
`Sam3Processor` exposes `add_geometric_prompt` for boxes only. `Prompt.append_points`
(`geometry_encoders.py:343`) is the supported way in and `Prompt` is a plain dataclass-ish object, so
the container adds an `add_point_prompt(points, labels, state)` next to the box call, in a small
subclass, so the single call to `_forward_grounding` stays in one place. Vendored code is not edited:
`vendor/` is upstream's and a local patch there is a patch to re-apply.

Points go in only if the matrix in P2 says they behave. **That is the rule for every parameter in
this plan: the declaration is what the model was measured doing, not what the code appears to
allow.**

Measured on 2026-10-18, and the answer is a qualified yes: points **run** — a point alone, points
with labels, and points beside text and boxes all answer `200` and return masks — but they steer
weakly on the fixture. A positive point beside the phrase raised the object count from 18 to 29 and
moved the largest object (IoU 0.00 against the text-only answer), and a negative point beside a point
raised 18 to 19 where a negative *box* took 16 down to 13. So `points` is published as accepted, and
no example on the site uses it, because "accepted" is what the container can promise and "useful" is
not yet measured. `boxes` passed every row and is the geometry the documentation shows.

**D11 — `imgsz` is exposed, as an enumerated allow-list, and a request above the ceiling is clamped
and reported, never refused.** The container owns the ceiling; the caller owns the choice below it.

A smaller size is welcome because throughput is the scarce resource here, not latency on one call:
the pool is one card at `max_in_flight = 1`, about 2.8 rps, and a participant looping over 500 rows
pays minutes at 1008 against a fraction of it at the smallest side. The measured small-object failure
(`gwhd`, 144 heads at about 57 px, zero detections at 1008 down to threshold 0.05) is the price a
caller pays knowingly, and the documentation shows it rather than hiding it.

```json
{"name": "imgsz", "type": "number", "values": [672, 784, 896, 1008], "above_maximum": "clamp"}
```

`values` rather than a stride formula because a formula is not a measurement — and the measurement
corrected this plan twice on 2026-10-18:

- The backbone's patch size is **14**, not 16. `vitdet.py`'s signature defaults to 16 and
  `model_builder.py:82` builds with `patch_size=14`; 1008 = 72 × 14, and 640 meets a grid that does
  not exist. Read the builder, not the default argument.
- `freqs_cis` is a **construction-time** buffer, so a model built at 1008 asserts on any other grid
  (`reshape_for_broadcast`). It is a pure function of the grid, so the container rebuilds it per side
  — and only for the four global-attention blocks, because the other 28 attend inside a 24 × 24
  window whose rope never moves. Measured: 640 and 768 answer `500`; 672, 784 and 896 answer `200`.

So the published list is the four sides the container was actually run at, which is what `values`
was for.

One rule, one sentence: **clamp down to the greatest declared value not above the request; below the
smallest, refuse.** 2000 → 1008, 800 → 784, 512 → `400`. The effective size is reported as
`resolution_used`, and a clamped request also carries a warning (D12), so nothing is done silently.

**D12 — A clamp is reported in the body, not as an error, and not only in the documentation.**
`ModelPrediction` gains `warnings: list[ModelWarning]`, optional and additive, where a `ModelWarning`
is `{code, message}` — the same two-part shape the error contract already uses, so a program branches
on `code` and a human reads `message`. (Named `ModelWarning`, not `Warning`, so the module does not
shadow the builtin.) It must be declared on the model or `_prediction`'s `model_validate` drops it;
the gateway relays it untouched and never fabricates one, because only the container knows its
ceiling and what it actually ran. First code: `resolution_clamped`.

`resolution_used` is the other half and already exists — it is the field that explains a mask's
blockiness — so a client that remembers what it asked can compare and needs no new field at all. The
warning exists for the caller that does not look, and because this is the only place in the API where
something other than what was asked is done: every other deviation is an error, and one silent
deviation would be worse. `response_model_exclude_unset` keeps the field out of answers that needed
no warning.

## The SAM 3.1 declaration this ends at

This is what the container declares today — read out of a running image, not drafted here:

```json
[
  {"name": "prompt", "type": "text", "default": "wheat head", "max_length": 200},
  {"name": "boxes", "type": "boxes"},
  {"name": "points", "type": "points"},
  {"name": "confidence_threshold", "type": "number", "default": 0.5,
   "minimum": 0.0, "maximum": 1.0},
  {"name": "imgsz", "type": "number", "values": [672, 784, 896, 1008],
   "above_maximum": "clamp"}
]
```

`boxes` and `points` are in this list only if P2's matrix passes them, and the matrix is in
`tests/prompts.py`: fourteen rows on `gwfss` row 4, each recording status, object count, largest-object
IoU against the text-only baseline, the size actually run, any warning, and the wall time. Every row
in it passes as a *call*; one reads as weak control (`text + point`), which is recorded above rather
than quietly dropped.

## What the SAM 3.1 container gains

- **One model, one processor per declared size.** `model` is loaded once; each `Sam3Processor` costs a
  `v2.Resize` and a few small tensors. `find_stage`'s `img_ids`/`text_ids` and its empty point and box
  slots are unrelated to resolution. `resolution` reaches nothing in the weights. Masks come back
  through `F.interpolate(out_masks, (img_h, img_w))` and the existing RLE path, so output geometry is
  unchanged. Memory falls as the size falls; it never rises.
- **Order of operations matters**, because `set_text_prompt` runs the forward immediately and
  `add_geometric_prompt` runs it again: `set_image` → `set_text_prompt` (if a text prompt was given) →
  one `add_geometric_prompt` per box → one `add_point_prompt` per point. The last forward is the
  answer. N geometry prompts is N forwards at about 0.36 s; four boxes is 1.4 s inside a 10 s budget.
  Appending all geometry and forwarding once is a later optimisation, not a contract question.
- `reset_all_prompts` is not needed: `run_prompt` builds `state = processor.set_image(image)` fresh
  inside the lock on every request.
- `objects_from_state(..., category=text)` becomes conditional on a text prompt having been given.
- `confidence_threshold` restored after the request, as today; the per-size processor restored the
  same way.
- `declared_parameters` grows `type` on each entry plus the `boxes`, `points` and `imgsz` entries.
- A clamp emits `warnings=[{"code": "resolution_clamped", "message": ...}]` and reports the size it
  ran in `resolution_used`.

## The measurement matrix (P2, and the evidence for the declaration)

The fixture, fixed by the user so results are comparable across runs:

**Where it runs: `labpc`, the local 3090, against the updated image — never through the pool.** The
gateway routes without caring which card answers, which is right in production and wrong as a
measuring instrument: a run through the pool mixes a 4090 and a 5090 into one table, and the numbers
this matrix turns on — does a negative point change the answer, does 640 agree with 1008 — are
exactly what differs at the margin between kernel families. One card, named, recorded in
`.agents/references/` with the driver and torch versions beside it.

```
POST /models/sam3.1-meta/predict?prompt=wheat%20head&confidence_threshold=0.25
     body {"dataset": "gwfss", "row": 4}
```

Run it once and keep the answer as the baseline. From that answer derive the geometry inputs:

- **positive points**: the centre of each returned `bbox`, and the centroid of its decoded mask.
- **negative points**: pixels where the row's **ground-truth mask is empty** — taken from the dataset
  surface, not guessed, so a negative point is provably not on an annotated head.
- **positive boxes**: the returned `bbox` converted to XYWH pixels (already the contract's shape).
- **negative boxes**: the tight box around an empty region of the ground-truth mask, chosen once and
  recorded, so the case is reproducible.

Then this matrix, one row per call, recording status, object count, area, IoU of the largest object
against the baseline, and latency:

| # | request | what it proves |
|---|---|---|
| 1 | `prompt` + `confidence_threshold` | the baseline still works after the change |
| 2 | `prompt` only | the container's default threshold |
| 3 | `boxes` (one positive) | a box alone, no text |
| 4 | `boxes` (positive + negative) | the label is read |
| 5 | `points` (one positive) | a point alone, no text |
| 6 | `points` (positive + negative) | the label is read for points |
| 7 | `prompt` + `boxes` | text and one geometry prompt |
| 8 | `prompt` + `points` | text and a point |
| 9 | `prompt` + `boxes` + `points` | **all three at once — the case the user asked about** |
| 10 | `prompt` + several boxes + several points | the sequence, not just the first element |
| 11 | nothing at all | the container's defaults |
| 12 | `2000`, `800`, `320`, `640` for `imgsz` | the clamp, the allow-list, the refusal |

Pass criteria, stated before the run so the result cannot be argued into the surface afterwards:

- rows 1–2, 7–11 answer `200` and produce at least one object;
- rows 3–6 answer `200`; a **negative** label must reduce the object count or its area against the
  same call without it, or that label is not published;
- row 9's largest object overlaps the baseline at IoU ≥ 0.9, or the combination is documented as
  accepted-but-unmeasured and does not become the example on the site;
- row 12: `2000` → `resolution_used: 1008` plus one `resolution_clamped` warning; `800` → 768; `320`
  → `400`; `640` → 640 with no warning, and its latency recorded next to 1008's.

Any row that fails removes its parameter from the declaration. That is what "only the parameters the
model actually runs" means here.

## Phases

**P0 — done (2026-10-18).** `gateway.md`: the parameter bullet now says the gateway validates against
the declaration and forwards verbatim, with no name compiled in; two bullets were added — a parameter
may not raise the admitted memory peak, and an undeclared model is not validated and says so through
`declared`. The older plan's "No `imgsz`" line and its decision-table row are marked superseded with
the pointer and the reason.

**P1 — done (2026-10-18).** `ModelParameter` grew `type`/`values`/`enum`/`max_length`/`required`/
`requires`/`excludes`/`above_maximum` and a `default` that carries a boolean; `ModelSummary.declared`;
`ModelWarning` and `ModelPrediction.warnings`; `MAX_QUERY_BYTES`; `validate_query` and
`_checked_query` in both routes; both `Query(...)` declarations deleted. 198 tests pass, `mypy
--strict` and `ruff` clean. `play.js` renders `values`/`enum` as a `<select>` and gives `boxes`/
`points` a shape hint. The 66 tests in `test_models_surface.py` include fifteen new ones, one per
refusal and one per relay.

**P2 — done (2026-10-18), on labpc's 3090 against the updated image and not through the pool.** The
four generations above, the point subclass (D10), one processor per size, the conditional `category`,
the warning, the typed declaration — then the matrix in `tests/prompts.py`. The declaration is
written from its results, and the results corrected the plan twice (D11: patch 14, a construction-time
rope cache). `models/sam31_official/processor.py` is new; `serve.py`, `infer.py`, the `Dockerfile` and
both READMEs changed. Submodule pointer not bumped yet: the rule is push before recording, and the
push is the user's.

**P3 — documentation site.** The parameter table on the model page generated at build time from the
live `GET /models` with markdown-exec, replacing the hand-written table that already duplicates the
declaration; one contract page for the `boxes`/`points` encoding; a short "what is not a parameter,
and why" note naming `device`/`half`, so a participant or their agent stops trying them; and the
measured size table from the matrix — latency and what happens to small heads at each size.

**P4 — the door for a second model (deferred, not built).** Nothing about validation is
SAM-specific, so the evidence that the door is open is a gateway test: a fixture declaration whose
shape differs from SAM's — a model with `conf`/`iou`/`classes`, no text prompt — validates through
the same path and forwards the same query. `models.json`, a container and a page are then a
configuration change, and that stays unbuilt until there is a model.

**P5 — clients.** The signature does not change: `predict(model, dataset, row, prompt="wheat head")`
is still the call. What changes is the **value encoding**, and it is not cosmetic: both clients turn a
list into a repeated parameter today — `global-wheat.js:229-235` loops
`Array.isArray(value) ? value : [value]` and appends one entry per item, and Python's `requests` does
the same with `params=`. So `boxes=[[480, 290, 110, 360, 1]]` would leave as five `boxes=` parameters
or one comma-joined string, and arrive as a 400 nobody can explain. In `predict` and `predict_file`
only, a non-scalar value is encoded once with `json.dumps` / `JSON.stringify` / a hand-rolled encoder
in Java; in `images` the repetition stays, because dataset filters need it. Three small helpers, one
test each, one docstring line each pointing at `GET /models/{model}`.

## Tests to write first (P1)

- `predict` forwards a parameter the gateway has never heard of, and the backend's answer comes back
  (characterisation of today).
- An unknown name is `400` and the message names the accepted set; the same for `predict/upload`.
- `confidence_threshold=2` is `400` from the declaration's bounds, not from a signature.
- a 300-character `prompt` is accepted while the total query stays under the cap; a 3 KB query is
  `400`.
- A model whose declaration is empty rejects every parameter; a model with **no** declaration
  forwards whatever it is given (`declared: false`).
- `boxes=[[1,2,3,4,1]]` and `points=[[1,2,1]]` are forwarded; `boxes=[[1,2,3,4]]` and `points=[[1,2]]`
  are forwarded too (the model's own all-positive default); `boxes=[1,2,3,4]` (not nested),
  `boxes=[]`, `boxes=[[1,2,3,4,2]]`, `points=[[1,2,3,4]]` and `boxes=[[1,2,3,"x"]]` are `400`.
- `?imgsz=672` is forwarded; `?imgsz=2000` is forwarded and relayed unchanged (the clamp is the
  container's); `?imgsz=320` is `400` with the accepted set.
- A backend that sends no `warnings` produces a body with no `warnings` key, and the gateway does not
  add one even when the request exceeded the ceiling — relaying is the whole job.
- `type: boolean` with `default: true` round-trips as a boolean through `GET /models`.
- The fixture declaration from P4 validates and forwards; no SAM-specific name appears in
  `models.py`.
- The OpenAPI document no longer carries `prompt` under either predict operation.
- An existing call — `?prompt=wheat head&confidence_threshold=0.5` — is byte-identical on the wire
  before and after.

## Risks

- **D6 is the only way this breaks a working call.** A container restarting while the gateway is up
  leaves `parameters: []`; without `declared` every parameter would be refused.
- Gateway and container ship independently: new declaration fields are additive, and an older gateway
  ignores them, so order does not matter.
- **Points depend on `Prompt.append_points` and one call to `_forward_grounding`.** Both are
  upstream's, both are reachable, and neither is a documented public API of the processor — the
  subclass in D10 confines that risk to one class, and the matrix decides whether points ship.
- **The rotary cache is shared state on the model.** One model, four processors, and the four
  global-attention blocks hold whichever side ran last. `activate()` must therefore run inside the
  inference lock, before `set_image`; a future path that runs two sizes concurrently would answer at
  the wrong grid. That is the reason the container is one lock deep, and it is written down here
  because it is a constraint that would otherwise look like a missing optimisation.
- `/docs` loses its visible parameters. Mitigated by the route description pointing at
  `GET /models/{model}`, and by the site's generated table being where a participant reads.
