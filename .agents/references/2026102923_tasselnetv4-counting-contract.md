# TasselNetV4: a counting answer on the model surface

What was built, what broke, and what a second counting model should copy. The constraints are in
[`rules/models.md`](../rules/models.md) and [`rules/gateway.md`](../rules/gateway.md); the agreed plan
is [`plans/2026102923_tasselnetv4-counting-model.md`](../plans/2026102923_tasselnetv4-counting-model.md).

## Why this model

TasselNetV4 is class-agnostic counting: it is given exemplar boxes on the image and reports how many
of that thing there are. Its answer is a density map whose sum is the count plus a 24×24 attention
map; it has no instances, no masks and no text prompt. That is a different answer from every model
the surface carried, and the gateway's `ModelPrediction` had no place for it — `_prediction`
re-validated a backend body against `objects` and dropped anything else, so a container could count
correctly and a caller would never see the number.

## What was measured

| fact | value |
|---|---|
| upstream | `tiny-smart/tasselnetv4` @ `c0bc80d5c51a57662238267deb1576acf4a62add`, Apache-2.0 |
| checkpoint | HF `akaxiao/TasselNetV4` @ `0f7ed8cd2b734906b7254ffa17e86cb1dc7fe5d1`, 1,128,783,566 B, sha256 `ef07c8ed…e378c` |
| load into the config-derived model | 0 missing, 0 unexpected tensors |
| parameters | 94.7 M; density per 384 tile 17×17, sum = count; attention 24×24 |
| image | `docker build` → 19.9 GB (siblings: 18.2 and 24.6 GB), checkpoint fetched and asserted inside the build |
| gates | gateway 305 tests; `tests/outputs.py` with no weights and no GPU; docs `--strict` clean |
| first real call | row `gwhd` 0, three exemplar boxes, on the 3090: `count` 30.83, 31 points, 0.2 s warm |

## Decisions that are worth carrying to the next counting model

- **`objects` stays required and empty.** Two optional fields (`count`, `points`) were added and
  nothing else changed; a client that already parses `objects` met no new key requirement, and the
  cache, lanes and upload route needed no edit.
- **The points are a visualisation, and the docs say so.** They are the `round(count)` highest
  attention cells, `score` = attention divided by this answer's maximum. Calling that a confidence
  would be a claim the model does not make.
- **The grid is the frame's, not the tile's.** Upstream resizes the frame to 384 px tall and pads it
  to a multiple of 384 before tiling, so one attention cell is 1/24 of the *resized* frame: on a
  1024-pixel picture points land about 43 px apart. An earlier draft of the docs said 16 px, which
  was the tile's number and wrong for every real image.
- **A count is floored at zero.** An exemplar drawn on something that is not the counted object pushed
  the density sum to -1.55 on `gwhd` row 1, a picture of leaves; the body publishes `0`, which is what
  the point selection already assumed. The docs and the container's README say it is the model's
  arithmetic rather than a finding of fewer than none.
- **`boxes` is the prompt and is required.** Extract-and-match has no negative exemplar, so a `0`
  label is a `400` rather than an ignored entry; accepting it silently would look like the model
  obeyed it.

## What broke, and what it teaches

- **The inner port is 12711, not the host port.** The first build bound 12713 inside the container,
  which breaks the pool convention (one host port per model, always mapped to 12711) and would have
  made the AWS security group's `12713:12711` map to nothing. The host port is the only per-model
  number.
- **The play page's own test polices words, not code.** `test_the_model_page_names_no_model_and_no_parameter`
  asserts the page text contains no parameter name, so an English sentence using "prompt" in a
  comment fails it. Write the comments without the vocabulary of the declaration.
- **Three upstream defects are absorbed by the compatibility layer, and none of them edits `vendor/`**:
  `cfg_test` carries an `xenhance` key the constructor rejects (so `test_checkpoint.py` fails as
  published); `forward` reads `samples[3][0]`, so `size` must be a one-element tensor; and the
  density head has no level above 128 px, so a large exemplar raises `UnboundLocalError` instead of
  a sentence — the container turns it into a `400`.
- **HF is the fetchable identity; the paper's links are not.** The Drive checkpoint answered "sign in
  required" and Baidu needs a client. The HF copy pins by commit, byte count and LFS oid, and is the
  same pattern the SegFormer container already used.

## VRAM, measured

On an RTX 3090 (24 GB) on 2026-10-30, the card's desktop already holding 14 GB:

| what | measured |
|---|---|
| the loaded process on the device, idle | 790 MiB (weights 362 MiB + CUDA context and cuDNN) |
| one request, torch's allocator peak | 428 MiB — 362 MiB of weights plus 66 MiB of activations |
| a 49-megapixel image (7000×7000) | 428 MiB |
| a 4000×500 panorama | 428 MiB |
| four requests in one process | 428 MiB each; reserved 478 MiB, no growth |

The peak does not track the picture because tiles are cut and run one at a time; what grows with an
image is CPU memory during the resize, which the upload's 50-megapixel cap bounds. `nvidia-smi`'s
per-process figure stays flat at 790 MiB during requests and cannot show a 66 MiB transient — the
allocator's own peak is the measurement that matters, taken inside the process
(`torch.cuda.reset_peak_memory_stats()` after load).

The first build shipped unmeasured bounds of 1.0 + 1.5 GB. They are now 0.8 + 0.1 GB in the labels and
in `serve.py`'s defaults, so the start-up check asks for 0.9 GB instead of 2.5 GB. On a card shared with
a desktop, that difference is the space a second workload gets.

## What the review changed

Six things, each one a duplicate or a wrong claim rather than a missing feature:

1. **The container bound 12713 inside the image.** The siblings bind 12711 and the host mapping carries
   the model number, so `-p 12713:12711` was the convention and the security group's port range assumed
   it. Fixed in `serve.py` and the `Dockerfile`.
2. **384 was written three times** — `infer.TILE`, `outputs.RESOLUTION`, `serve.RESOLUTION`. Now one
   definition, imported by the other two.
3. **A comment using the word "prompt"** broke `test_the_model_page_names_no_model_and_no_parameter`,
   which asserts the page text carries no parameter name. The page's vocabulary has to stay clear of the
   declaration's.
4. **`catch (error)` with an unused binding** in the pointer-capture fallback.
5. **The caption read "0 objects · no area or RLE to check"** under a counting answer, which reads as a
   model that found nothing. A counting answer now has no check line; the count beside it is the answer.
6. **The docs said a point is 16 px.** That is the tile's figure; the frame is resized to 384 px tall
   first, so on a 1024-pixel picture points are about 43 px apart.

Verified after the fixes: 305 gateway tests, `tests/outputs.py`, `mypy`, `ruff`, docs `--strict`, a
rebuilt image serving `/health`, and `/play` in Firefox against the live container — three boxes drawn
in the parameter input appear on the picture as they are typed, and Run answers `count 30.8` with 31
dots.

## Deploying it to the three backends

One 31 MB tarball of the folder (no `.venv`, no weights) plus `docker build` on each machine; the
checkpoint arrives on that machine's own downlink, so nothing of 19.9 GB crosses the sender's uplink.
Two things cost a round trip each:

- **The gateway host runs from git.** The host's checkout was at the same commit as this one, so the
  `count` and `points` fields could not arrive there until the change was pushed and pulled. Until it
  was, the container answered 30.83 and the public API answered a body with no `count` at all — the
  fields were dropped by the host's older `ModelPrediction`, which is exactly the failure the plan
  predicted. The answer is a reminder that "the container works" and "the surface serves it" are two
  deployments.
- **A background build inside WSL 2 dies with the SSH session.** `nohup … &` inside `wsl -e bash -lc`
  returns, the session closes, WSL shuts the distro down, and the build, its log and `/tmp` go with
  it — the log was the only evidence, and it had vanished. Run the build in the foreground of a long
  ssh (`ServerAliveInterval`) instead, or keep the VM alive with a process that outlives the client.

The cache had to be cleared once: an answer stored before the host was updated holds no `count`, and
a cache hit would have kept serving that body. The store is disposable by rule, so the file was
deleted rather than migrated.

## What is left

- **Registering it on the gateway host.** `models.json` gains `"tasselnetv4": ["http://<address>:12713"]`
  and the gateway restarts; the row belongs in `backend-pool.md` only with a dated observation.
- **A model page on the docs site.** `fixtures/model-directory.json` has to be refreshed from an
  authenticated `GET /models` after that registration, and `make_models.py` writes the table.
- **The VRAM figures are bounds, not measurements** — they are labelled as such in the image and the
  README. A first real run on a pool card should replace them.
