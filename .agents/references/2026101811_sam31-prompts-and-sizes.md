# SAM 3.1's parameters: boxes, points, and four sizes — what the card said

Plan: [`plans/2026101811_model-parameter-contract.md`](../plans/2026101811_model-parameter-contract.md).
P0 and P1 (the gateway) are in
[`2026101811_model-parameter-validation.md`](2026101811_model-parameter-validation.md); this is P2, the
container, run on **labpc's 3090 against the rebuilt image**, not through the pool — a 4090 or a 5090
answering instead moves exactly the numbers this measures.

## What changed in `models/sam31_official`

- **`processor.py` is new.** `Sam31Processor(Sam3Processor)` adds `add_point_prompt`, because upstream's
  processor exposes `add_geometric_prompt` for boxes and nothing for points while `Prompt.append_points`
  has always been there. It also carries one rotary cache per size.
- **`serve.py`**: `boxes`/`points`/`imgsz` query parameters, a JSON parser for each geometry shape, one
  processor per declared size behind one loaded model, `category` only when a text prompt was given, and
  a `warnings` entry when `imgsz` was clamped.
- **`infer.py`**: `load_processor` builds a `Sam31Processor`; `processor_for` builds a sibling for another
  size over the same model.
- **`tests/prompts.py` is new**: the fourteen-row matrix. Like `tests/outputs.py` it is a script, not a
  pytest case — it needs weights, a card and the network.

## The two things the measurement corrected

**The patch size is 14, not 16.** `vitdet.py`'s `Attention` signature defaults to `patch_size=16`, which
is the number I read first; `model_builder.py:82` builds this backbone with `patch_size=14`, and the
trained side is 1008 = 72 × 14. Every "multiple of 16" size (640, 768) meets a grid that does not exist.

**`freqs_cis` is a construction-time buffer.** It is built once in `Attention._setup_rope_freqs` from
`input_size`, so a model constructed at 1008 asserts in `reshape_for_broadcast` the first time another
grid arrives — measured as a `500` on the first 640 request, with the assertion in the log. It is a pure
function of the grid (`compute_axial_cis`), so rebuilding it per side is the same computation the
constructor ran — but **only the four global-attention blocks** need it: this backbone is windowed
(`window_size=24`), and 28 of its 32 blocks attend inside a 24 × 24 window whose rope never moves.
Rebuilding every rope block — my first attempt — silently rewrote the windowed ones and broke 1008.

Three more failures on the way, each worth knowing:

1. `_setup_rope_freqs` *registers* the buffers it builds, so building is not read-only. `build_rope_cache`
   now captures the values already on the module and puts them back before returning, which is what makes
   it safe to call for a side nobody is about to run.
2. The rebuilt tensors are created on the **CPU**, and the model was moved to the card after construction:
   `RuntimeError: Expected all tensors to be on the same device`. Each rebuilt buffer is moved to the
   device of the buffer it replaces.
3. A processor that installs nothing is not neutral: the blocks are shared, so whatever side ran last is
   what they hold. The base size needs its own cache too, or the request after a 672 request answers at
   672 while believing it is 1008. `activate()` therefore runs for every size, inside the lock.

## The matrix, `gwfss` row 4, `prompt=wheat head&confidence_threshold=0.25`

Baseline: 18 objects in 0.21 s at 1008. The fixture is 512 × 512; the negative point is a leaf-class
pixel from the row's own palette mask, so it is provably not on an annotated head.

| # | request | status | objects | IoU vs baseline | notes |
|---|---|---|---|---|---|
| 1 | text + 0.25 | 200 | 18 | — | the baseline |
| 2 | text, default threshold (0.5) | 200 | 0 | — | this row needs 0.25; a fact about the fixture |
| 3 | one exemplar box | 200 | 16 | 0.98 | |
| 4 | box + negative box | 200 | 13 | 0.98 | the label subtracts: 16 → 13 |
| 5 | one positive point | 200 | 18 | 0.98 | |
| 6 | point + negative point | 200 | 19 | 0.97 | the label does **not** subtract here |
| 7 | text + box | 200 | 13 | 0.98 | |
| 8 | text + point | 200 | 15 | **0.00** | 29 objects when the point raised confidence; the largest object moves |
| 9 | text + box + point | 200 | 13 | 0.98 | all three at once: fine |
| 10 | text + 2 boxes + 2 points | 200 | 14 | 0.98 | |
| 11 | nothing at all | 200 | 0 | — | container defaults |
| 12a | `imgsz=672` | 200 | 15 | 0.97 | 0.13 s |
| 12b | `imgsz=800` | 200 | 22 | 0.97 | ran at 784, `resolution_clamped` |
| 12c | `imgsz=2000` | 200 | 18 | 1.00 | ran at 1008, `resolution_clamped`, identical answer |
| 12d | `imgsz=512` | 400 | — | — | below the smallest measured side |

Read together: **boxes behave as exemplars and points do not steer reliably.** Row 9 is the one worth
keeping — text + box + point all at once returns the box-only answer, so the combination is safe. Row 8
is why `points` is published as *accepted* and used in no example: a positive point beside a phrase
changes the answer in a way nobody has characterised. Row 12b/12c are the clamp working as specified,
including the case where a caller asks for more than the card admits.

## Reproduction

```bash
cd models/sam31_official
docker build -t sam3.1-meta .                 # weights are cached in a layer, so this is the code only
docker run -d --gpus all -p 127.0.0.1:12712:12711 \
  -e BACKEND_TOKEN=test-token -e SAM31_MACHINE=lab-3090 --name sam31-test sam3.1-meta
uv run tests/prompts.py --base http://127.0.0.1:12712
```

`tests/prompts.py` writes `build/prompts.json` (ignored) and prints the table. `SAM31_SIZES` overrides
the four sides; `_sizes` refuses to start on a value that is not a multiple of 14, which is the check
that would have saved the 640 experiment.

## The site (P3)

The parameter table on the model page is **generated from a recorded declaration**, not transcribed:

- `docs/scripts/fixtures/model-directory.json` is a real `GET /models` answer. Recording it means
  pointing a gateway at the running container and asking once — the API needs a key, so a *docs build*
  cannot do it, and that is the reason this is a fixture rather than a live fetch. A site built from a
  participant-facing repo must not hold a secret or depend on a host being up.
- `docs/scripts/make_models.py` writes the table between `<!-- parameters:start/end -->` on a model
  page: a `default` column, an `accepts` column derived mechanically from the declaration
  (`values` → the list plus the clamp, `minimum`/`maximum` → a range, `max_length` → a character
  bound), and the model's own description. Re-running it is idempotent.
- `.github/workflows/docs.yml` runs it before the build and diffs `docs/content/models/`, the same
  stale check the quickstart notebook gets. A container whose declaration moved and a page that did
  not is a failing build.

**markdown-exec does not work under Zensical**, which cost an hour and is worth not repeating: added
as `[project.plugins] markdown-exec = {}` it does execute the block, but renders the *result* as a
fenced code block — `result="markdown"` and `result="html"` both reach the reader as escaped text, and
as a plain markdown extension it has no `makeExtension` at all. The generated-file-plus-stale-check
pattern is what the repository already trusts.

Content changed: the model page gained **Prompting** (text/box/point, with the measured numbers) and
**Size** (the four sides, the clamp, latency against IoU); the models index now says what is *not* a
parameter and why; and `api/http.md` carries the encoding once under **Model parameters**, which the
model page links to instead of repeating.

## Left open

- **The submodule pointer is not bumped.** `rules/models.md` says push before recording, and the push is
  the user's.
- **`tests/outputs.py` cannot find its decoder.** Its default path is
  `docs/content/post-processing/rle_decode.py`, which the docs restructure removed; the decoder now lives
  inside `clients/python/global_wheat.py`, which imports `requests` and so cannot be loaded inside the
  image as-is. The container's own smoke test needs a decision: publish a standalone decoder again, or
  carry a copy under `tests/`. Not touched here, because it is a different failure from this one and
  guessing a path would hide it.
- **Points want a better fixture.** `gwfss` row 4 is 33 % head; a point's effect cannot be characterised
  on a scene where every pixel is near an object. The row that would settle it is a sparse image with one
  conspicuous head, and `gwhs-temp` row 0 already is one.
