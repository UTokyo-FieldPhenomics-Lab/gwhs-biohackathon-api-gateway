# Pre-release API conformance pass

A participant's-eye test of the documented contract on 2026-11-06, before the event: every route the
site promises, every model id, every client method, and the abnormal calls a hall full of people will
produce. The question was not "does the code compile" but "does a reader who follows the site get an
answer, and does a wrong request get a refusal the site describes".

## What was run

Against the live host with a participant token for team `dev`, in three passes:

- **Raw HTTP**: 190 checks over auth, datasets, image detail, the `data.*` plane, the model directory,
  model parameter validation, uploads, generations, the result cache, per-team lanes, filters,
  paging, and transport behaviour (`HEAD`/`OPTIONS`/`TRACE`, CORS absence, wrong `Content-Type`,
  invalid JSON, a 4 KB body, a 16 MB upload, a 63-megapixel PNG).
- **Models**: every id that had a machine, a documented call on each answer shape (masks, semantic
  classes, a count and its density field, a backbone's tokens, COCO boxes and masks, prose and
  pictures), plus one upload per id and one generation per Nano Banana id; the two picture answers
  were checked for `frame`/`reframed`.
- **Clients**: the documented quickstart executed end to end against the live API with its 13
  recorded outputs compared, the Python live suite, and ad-hoc live calls of every method from the
  JavaScript (Node) and Java clients. Unit suites: gateway, datasets, Python clients, `node --test`,
  `Checks.java`.

Two probe scripts lived in `/tmp/opencode/probe/` and were not committed: they are a test run, not a
maintenance surface. The durable result is the tests added below.

## Result

All documented calls work. Every dataset row sampled resolves its `image_url` and `mask_url`, and the
bytes agree with the row's `width`, `height` and `mask_url`; every published count and split sums; no
page repeats or skips a row; every filter key selects the rows it claims. Every live model id answers
its documented shape, the counting sum matches its field, the backbone grid decodes to the declared
`dim`, the semantic palette matches the dataset's, and the cache, lane ceiling and upload refusals
behave as the site says. The three client files call every documented method successfully, including
uploads, frame cropping and palette splitting.

Thirteen of the 190 checks did not pass at first. Eleven were the probe's own wrong assumptions
(`boxes=[]` is refused with a clear sentence, a negative row is `404`, gwfss images are PNG, a
semantic detail carries no `objects`, and so on) and are now recorded as the correct behaviour. Two
were real and are written down here.

## What needs attention

### 1. `internvl-u` is documented and served by nothing

**Closed 2026-11-06 03:04 — the container is up and passed a full pass; see the follow-up at the
end of this record.**

`GET /models/internvl-u` answers `backends_up: 0`, `declared: false`, no `resolution` and an empty
`parameters` list; every call answers `503 unavailable`. The model page publishes three parameters
(`prompt`, `mode`, `size`) and the models index opens with "Nine models answer today". Eight do.

This is the one place the docs fixture and the live directory disagree: `docs/scripts/fixtures/model-directory.json`
records the declared parameters, so the generated parameter table promises validation the gateway
cannot perform while the container is absent. A participant following that page spends their first
five minutes on a 503.

Either bring the container up before the event, or remove the page and index row until it is. The
docs rule that a new id is served *before* its page exists covers exactly this case.
[Models rules](../rules/models.md), [docs rules](../rules/docs.md).

### 2. A restart is a 29-second window with an empty `502`

The service was restarted at 16:44:42 UTC while these tests were running. The new process logged
`Started` at 16:44:47 and `Uvicorn running` at 16:45:16 — 29 seconds in which Caddy had no listener
and answered **`502` with `content-length: 0`**. The site promises one refusal body for every
refusal; a participant's client sees an empty response at the one moment the host is coming back.

The delay is the startup declaration read: `DECLARATION_TIMEOUT_SECONDS` is 3 s and backends are
asked one after another, so a pool with a handful of switched-off containers spends tens of seconds
before the socket is bound — precisely the machines the timeout exists for. The docstring says an
off backend "appears in the directory with nothing declared rather than holding up the process", but
it does hold the process up, before it serves `/health`.

Two separable remedies: read declarations concurrently (one thread each, the same 3 s ceiling), and
give Caddy a body for an unreachable upstream (`handle_errors` with the contract's JSON), so even a
crash answers in the published shape. The first is the real fix; the second keeps the contract true
while the first is failing for another reason.

A reboot is in scope: `systemd` starts the unit, and this is its first 29 seconds.

### 3. Three gates were red before this pass

All three are fixed here:

- `clients/python/tests/test_quickstart.py` executed the quickstart's shell cell. The cell now opens
  with a comment above `!curl`, and the guard tested `source.startswith("!")`, so the cell reached
  `compile()` and raised `SyntaxError`. The guard now looks for any line that starts with `!`, which
  is not valid Python anywhere.
- `clients/python/tests/test_live.py` asked `models[0]` — `dinov3-vith16plus` in the directory's
  order — for `prompt`, and a backbone accepts `side`. It now asks a model that declares `boxes` or
  `points`, so the smoke test exercises a promptable segmenter whatever the directory order is.
- `uv run ruff format --check .` failed on `src/biohack_gateway/app.py`: the last commit left a
  second blank line inside `example_route`. One blank line removed; the gate is green again.

### Smaller observations, not defects

- **`?split=train&split=validation` is documented as "means both, never either" and answers an empty
  page.** The semantics are `and` and that is what the sentence says, but no image is in two splits,
  so the example teaches a reader that the filter is broken. A tag pair would show the same rule with
  rows in it (`?tags=Labo&tags=healthy`). `test_two_values_of_one_filter_narrow_rather_than_widen`
  pins the semantics either way.
- **`POST /models/sam3.1-meta/generate` answers `502 upstream_error` "the backend does not serve this
  model".** The backend did say that, and `_refusal_status` documents a `404` as an operator-facing
  configuration fact. But a caller who used the wrong route is told the machine is at fault. Either
  teach the declaration which routes a model serves, or say in the refusal table that `generate` on a
  model with no picture is this `502`.
- **A single `refused` connection takes the only backend of a model out for ~90 s, and the refusal
  says "no backend is answering".** The probe meanwhile reported the machine alive. This happened
  once, during a DGX reconfiguration that was moving containers, so the trigger was probably that
  work rather than load; the wording is still wrong for a passed-over machine, and `/pool` is the only
  place that tells the two states apart.
- **A hosted edit can answer `400 "the image model produced no picture (STOP)"`.** The provider's
  finish reason reaches the participant unchanged, and "STOP" is not a sentence. This is the one
  case where a `400` is not really the request's fault; the docs example avoids it by using a
  portrait crop, but a participant editing a `gwhd` plot will meet it.

## Tests added

- `gateway/tests/test_datasets_surface.py`: two distinct values of one filter narrow (and's two
  consequences, one empty); non-integer row paths are `400` and a negative one is `404`.
- `gateway/tests/test_models_surface.py`: `generate` without its required prompt; `generate` with an
  unpublished enum value, an over-length prompt, a repeated parameter and an undeclared one; an
  upload with no file part and under a wrong field name; a predict body that is invalid JSON, empty,
  or not `application/json`.
- `clients/python/tests/test_quickstart.py`: the shell-cell guard and the setup's output assertion,
  which also had to allow the version line the three-part quickstart now prints.
- `clients/python/tests/test_live.py`: the inference half picks a promptable segmenter.

## Commands

```sh
cd gateway && uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
cd datasets && uv run pytest
cd clients/python && uv run pytest
cd clients/python && GLOBAL_WHEAT_LIVE=1 GLOBAL_WHEAT_API_KEY=... uv run pytest tests/test_live.py
cd clients/js && node --test tests/
javac -d /tmp/opencode/global-wheat-java-checks clients/java/GlobalWheat.java clients/java/tests/Checks.java
java -ea -cp /tmp/opencode/global-wheat-java-checks Checks
```

`gateway` now runs 356 tests, the Python clients 48 with the two live ones skipped by default,
`datasets` 80, `node --test` 48, and `Checks.java` all pass. The quickstart's 13 recorded outputs
reproduce exactly against the live API, so
`docs/scripts/record_notebook.py` was not needed and no figure or recorded output changed.

## What to do differently

- Run this pass **after** the pool is frozen, not while a card is being reconfigured: one observed
  `503` and one `refused` in this record are the deployment moving, not the API failing, and
  separating them cost a `/pool` watch.
- Probe scripts belong in `/tmp`; only the cases that fail a contract belong in the suites. The
  abnormal matrix is now mostly in `test_models_surface.py` and `test_datasets_surface.py`, so the
  next pass can start from the suites rather than from a new script.
- Check `content-length: 0` on a `502` first when a call fails: an empty body means Caddy, not the
  gateway, and sends you to `systemctl` rather than to the request.

## Follow-up: `internvl-u` came up

At 03:04 on 2026-11-06 the container answered on `lab-intel3090` in `bfloat16`, with one backend,
`declared: true`, `resolution: 1024` and the three parameters the page documents: `prompt` (required,
no default), `mode` (`text` by default) and `size` (512/768/1024, default 1024). The docs fixture and
the live declaration agree field for field, so the generated parameter table is true again.

Fourteen checks passed, the same matrix the earlier pass used on the other ids:

- `mode=text` answered 497 characters of prose with `objects: []`;
- `mode=image` answered a PNG, `mode=text_image` answered prose and a picture;
- `size=512` answered no larger than 512, and `size=2048` ran at 1024 with `resolution_clamped`;
- `mode=text` with a `size` still answered prose (the size is ignored, as documented);
- an upload was edited and answered a picture;
- `prompt` missing, an unknown `mode`, a 301-character prompt and an undeclared parameter were all
  `400 invalid_request`;
- the same question twice was cached on the second call.

The wait was 7 s for text, 10–17 s for a picture, and 15 s for an upload — inside the 120-second
budget the container now declares, and enough that the ten-second default it shipped with would have
cut every picture short, which is what the gateway rule records.

**One wording gap.** On the `gwhs-temp` sample (592 × 2509, 1:4.24) `mode=image` at 1024 answered
256 × 1024 and at 768 answered 256 × 768: the shorter side stays at 256, so the second answer is 1:3
rather than the input's 1:4.24. On a square `gwhd` image the size is exact (768 × 768 at 768, 512 ×
512 at 512). The parameter table says "the shorter side follows the input's aspect ratio", and a
256 px floor — or a 1:3 cap on extreme ratios — is not written down. A participant overlaying the
answer on a tall spike photograph would need to know it.

The play page also generates the internvl call correctly now: `prompt`, `mode` and `size` reach the
query, and the generated Python and JavaScript calls and the curl command all answered in `text`
mode.
