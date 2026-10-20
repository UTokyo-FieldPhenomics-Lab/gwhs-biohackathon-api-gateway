# Bytes, cache, admission: the final plan

Supersedes [`202610192330_✅lane-width.md`](202610192330_✅lane-width.md). The scheduler work is
finished; this plan spends nothing more on ordering and everything on what crosses the Pacific.

## Steps

- [x] Cache row answers in SQLite; a hit takes no lane
- [x] Python client resizes before sending (Pillow, EXIF-aware)
- [x] JS client splits into core + web (canvas) + node (sharp)
- [x] Java client resizes before sending (ImageIO + EXIF orientation)
- [x] Resizing is an option (`resize_before_submit`, on by default); the original can be sent untouched
- [x] Live equivalence check: a resized upload and the original answer alike
- [x] Client-side benchmark per language; numbers in `references/`, not in the docs
- [x] Docs and the 413 message say what the model actually sees
- [x] Upload admission: a byte budget, a fair queue, `Retry-After`
- [x] `GET /pool`: the numbers routing uses, visible to an operator
- [ ] 50-team mixed load test; record the comparison

## What the measurements say

- **The card is not the constraint.** Row mode 24 %, upload 9 % mean utilisation; 47–53 GPU-ms per
  request against 675/2585 ms end to end.
- **Ordering is within noise.** The last three changes moved throughput 3–7 %; row p95 moved 13 %
  between identical runs.
- **Upload is linear in bytes.** Measured through the production gateway, from the host (only the
  Pacific leg): 950 KB → 2.71 s, 4452 KB → 4.60 s, 9757 KB → 8.03 s. That is `1.7 s + 0.65 ms/KB`,
  i.e. 1.55 MB/s for one stream, against ~2.2 MB/s aggregate when twelve streams share it.
- **15 MB is coherent for one request** (about 11.4 s) and impossible for fifty (500 MB ≈ 4 min).
  The limit is not the bug; the aggregate is.
- **The gateway has two cores.** A 12 MP decode/resize costs 150–300 ms of one core, or 20–40 ms
  with `Image.draft` for JPEG only. At 5 uploads/s that is 0.75–1.5 cores: it would starve the proxy.

## Decisions

1. **Caddy keeps `max_size 15MB`.** Only the 413 message changes: "resize it to about 1008 px; the
   model runs at that size". The limit is a RAM guard, not a time guard; the time guard is admission.
2. **No image decoding in the gateway.** Resize happens on the participant's machine, where there
   are fifty of them. Python, the browser and Java can; Node gets `sharp`.
3. **Resizing is an option, and its size belongs to the model, not the caller.** Every client takes
   `resize_before_submit` (`resizeBeforeSubmit` in JS and Java), on by default; `False` sends the
   original bytes untouched. The target side is the model's own: `imgsz` where the caller passes it
   (`sam3.1-meta` declares it, `gwfss-segformer-b4` does not), otherwise the `resolution` the model
   published in `GET /models/{id}` — 1008 or 512. Two earlier names were dropped. `max_side` would
   duplicate `imgsz`, and the two could contradict each other, sending fewer pixels than the model
   is about to use. Plain `resize` did not say *which* resize, in a system where the GPU resizes
   every input anyway; the flag names the one the caller controls.
4. **The cache is for row predictions only**, keyed by the published revision, and a hit never takes
   a lane. Uploads are never cached.
5. **Admission applies to uploads only.** Fifty concurrent row requests are absorbed by the thread
   pool and the cache; only bytes need a queue.
6. **Ordering, `DELAY_PER_QUEUED_REQUEST` and `max_in_flight` do not move.** Nothing measured
   justifies touching them.

## Task 1 — the cache

New `gateway/src/biohack_gateway/cache.py`: `ResultCache(path, max_bytes)`.

- SQLite, `journal_mode=WAL`, `synchronous=NORMAL`, one connection per thread.
- `answers(key TEXT PRIMARY KEY, request TEXT NOT NULL, body BLOB NOT NULL, computed_at TEXT NOT
  NULL, bytes INTEGER NOT NULL)`.
- Key: `sha256` of canonical JSON `{dataset, revision, row, model, query}`. `revision` is the
  `revision` column the index already carries; a republish changes it and the cache goes cold.
  `query` is the literal query string, sorted by name — two spellings of the same number are two
  entries, which is a documented, harmless miss.
- Default path `~/.config/biohack-gateway/cache.sqlite` beside `index/`; `BIOHACK_CACHE_PATH`
  overrides, `BIOHACK_CACHE=off` disables, `BIOHACK_CACHE_MAX_BYTES` defaults to 2 GB.
- Prune oldest `computed_at` on startup and every 1000 writes while over the cap.
- Only a `200` from a backend is stored.

`models.py`:

- `ModelPrediction` gains `cached: bool = False` and `cached_at: datetime | None = None`. The route
  already sends `response_model_exclude_unset=True`, so a live answer is unchanged on the wire.
- `ComputedBy` inside the stored body is returned untouched: it is the reproducibility record of the
  machine that actually ran, not of the cache.

`app.py`:

- `create_app(..., cache=...)`; build it beside the registry and close it on shutdown.
- `predict`: resolve the address and the query, compute the key, look up. A hit returns
  `_prediction(...)` with `cached=True, cached_at=...` and **takes no lane**. A miss acquires the
  lane, forwards, stores, releases.
- `predict_upload`: acquires the lane first, as today; never touches the cache.
- The per-token gate moves out of the middleware for `predict` only. Keep the middleware for auth and
  for `predict_upload`; factor the acquire/refuse into one helper so the 429 body exists once.
- One log line per request: `cache=hit|miss model=... row=... ms=...`.

Tests (`gateway/tests/test_cache.py`):

- a second identical call answers from the cache and the fake transport is called once;
- the hit carries `cached: true` and the original `computed_by`;
- a different row, query, revision or model misses;
- three hits while three lanes are busy all answer (no lane taken);
- pruning respects the cap and keeps the newest;
- `BIOHACK_CACHE=off` answers every call from the backend.

Acceptance: a repeated `predict` answers in < 20 ms and writes no `inference` line.

## Task 2 — the clients

**The GPU side does not check; it always resizes.** `sam31_official` hands the image to its
processor, whose transform resizes it to 1008; `gwfss_segformer` hands it to the HuggingFace
`AutoImageProcessor`, which resizes it to 512. Neither has an "already fits" branch. Adding one
would save a few milliseconds of host CPU on one machine while adding a second preprocessing path to
test against the model's own transform — not worth it. What matters is that the client's target is
exactly the size the model would have chosen, so the container's resize is the one it would have
made anyway.

**Python** (`clients/python`):

- `pillow>=11` in `pyproject.toml`.
- `_prepare_upload(image: str | Path | bytes, target_side: int) -> bytes`: open, `exif_transpose`,
  resize only when `max(w, h) > target_side`, `convert("RGB")`, JPEG q90. An image already small
  enough passes through **byte-identical** — no re-encode, no EXIF loss.
- `predict_file(model, image, resize_before_submit=True, **params)`. The target side is derived,
  never invented: `min(imgsz, declaration ceiling)` when the model declares `imgsz` and the caller
  passes it, otherwise the model's published `resolution`. The declaration is fetched once per
  client and cached; if it cannot be fetched, the client sends the original and lets the predict
  call report the error. `resize_before_submit=False` sends the original bytes untouched, which is
  the documented opt-out.
- Tests: a 4000×3000 fixture tagged orientation 6 uploads as a portrait ≤ the declared side; a small
  JPEG is sent unchanged; the decoded output has no EXIF orientation left;
  `resize_before_submit=False` produces `bytes_sent == len(photo)`; `imgsz=504` sends a 504-side
  image; `gwfss-segformer-b4` sends 512, not 1008.

**JavaScript** — one core, two entries, no build step:

- `clients/js/global-wheat.js`: today's file plus `Client({..., resizer})` and
  `predict_file(model, image, {resizeBeforeSubmit = true})`; `resizeBeforeSubmit: false`
  sends the original. No image code.
  The target side comes from the same derivation as Python: `imgsz` when given, else the model's
  published `resolution`.
- `clients/js/global-wheat.web.js`: ~30 lines, a canvas resizer
  (`createImageBitmap` + `OffscreenCanvas.convertToBlob`, fallback to `<canvas>.toBlob`), subclass
  and re-export.
- `clients/js/global-wheat.node.js`: ~30 lines, a `sharp` resizer
  (`.rotate().resize({width, height, fit: "inside", withoutEnlargement: true}).jpeg({quality: 90})`),
  subclass and re-export.
- `package.json`: `sharp` as a **devDependency** so the node path can be tested; the test skips when
  it is not installed. Nothing is added to the shipped files' imports except in the node entry.
- `docs/scripts/sync_clients.py`: copy all five client files.
- `docs/content/api/clients.md`: a JavaScript row per entry, the `npm i sharp` note, and the module
  import examples updated to `.web.js` / `.node.js`.
- Tests: the web resizer against stubbed `createImageBitmap`/`OffscreenCanvas`; the node resizer
  gated on sharp; `resizeBeforeSubmit: false` sends the original bytes.

**Java** (`clients/java`):

- `predict_file(..., boolean resizeBeforeSubmit = true)`; the same derived target side, fetched
  from `GET /models/{id}`; `false` disables.
- `ImageIO` read; a minimal APP1 reader for the EXIF orientation tag (0x0112) — the JDK has no EXIF
  parser and `ImageIO` drops the tag on write, so without this a portrait phone photo arrives
  sideways, which is worse than a slow one; rotation by `AffineTransform`, resize with `Graphics2D`
  bicubic, JPEG writer at 0.9.
- Legacy TIFF/WebP inputs fall back to sending the original bytes.
- Tests: an orientation-6 fixture comes back upright and no wider than the declared side; a small
  image is unchanged.

**The equivalence check** — resizing must not change the answer, and that is a test, not a promise
(`clients/python/tests/test_live.py`, behind the existing `GLOBAL_WHEAT_LIVE=1` gate):

- take a published row whose width exceeds 1400 px, download its image, and call `predict_file`
  twice: once with `resize_before_submit=False`, once with the default;
- assert the resized upload really was smaller (`len(small) < len(photo) / 3`);
- compare the two answers: `resolution_used` equal; object count within ±1; each class's total area
  within 10 %; every object above half the top score has a match at normalised-box IoU ≥ 0.85
  (boxes normalised by each image's own width and height, because they are reported in the source
  image's pixels).
- The JS and Java clients go through the same server path with the same bytes, so one live check
  covers the contract; their own unit tests cover the mechanics (EXIF, size, opt-out).

**The client-side cost is measured, not assumed.** `resize_before_submit=True` moves work onto the
participant's machine, and the failure this guards against is a client that spends seconds resizing
before it sends anything. Each client's tests carry a benchmark, run on `labpc`:

- fixtures: a 4000×3000 JPEG (12 MP) and a 4000×3000 PNG — the JPEG exercises `Image.draft`, the
  PNG is the slow path with no shortcut; both are generated by the test, never committed;
- measure the whole preparation (open, EXIF transpose, resize, encode): one warm-up, then the median
  of five, and also the pass-through case where the image is already at the target and must cost
  under 10 ms;
- assert a generous ceiling — Python 400 ms, JS 200 ms, Java 800 ms after the JVM has warmed — and
  print the median and the output size. The ceiling catches "seconds", not a 20 % regression. A
  prototype measured on `labpc` with Pillow, before the client exists: 114 ms for a 12 MP JPEG and
  219 ms for a 12 MP PNG, both to about 340 KB, and 0.02 ms to read the header alone — the ceilings
  sit two to four times above what this machine already does;
- the files: `clients/python/tests/bench_resize.py`, `clients/js/tests/bench.test.js` (skipped when
  `sharp` is absent), `clients/java/tests/Bench.java`.

The numbers go in `.agents/references/`, not in the published docs: they describe one machine, and
the docs should not promise a latency a participant's laptop may not meet.

**Docs** (`docs/content/api/http.md`, `docs/content/examples/quickstart.md`):

- The upload section states the model's resolution (1008 for `sam3.1-meta`, 512 for
  `gwfss-segformer-b4`) and that a larger image buys nothing.
- The quickstart upload cell resizes with the Pillow it already uses for display.
- The limits table stays at 15 MB; the 413 row points at the resize snippet.

## Task 3 — upload admission

New `gateway/src/biohack_gateway/admission.py`.

- `LinkRate`: rolling 60 s of `bytes completed / busy seconds` (busy = at least one upload in
  flight), seeded 1.5 MB/s, clamped to [0.2, 6] MB/s. Measured, not assumed: a bad night at 田無
  tightens admission by itself.
- `UploadQueue`: a `threading.Condition` and a list of waiters ordered by
  `(team_in_flight, arrival)`. A waiter is admitted when `bytes_in_flight + body <= budget`, where
  `budget = min(24 MB, LinkRate × 12 s)`. Wait at most 20 s; then `429` with
  `Retry-After: ceil(estimated_wait)` and the existing error envelope.
- Wired into `predict_upload` before `registry.forward`; the per-token cap still applies first.
- Release notifies the queue and feeds `LinkRate` with the completed bytes and duration.
- Rows are untouched.

New `GET /pool` (keyed): per machine `machine`, `models`, `alive`, `rtt_ms`, `in_flight`,
`bytes_in_flight`, `bytes_per_s`, `probed_seconds_ago`; plus `cache: {entries, bytes, hits, misses}`.
Not part of the participant contract.

Tests: FIFO within a team, a team with nothing in flight overtakes a team with two, the byte budget
admits and blocks correctly, the wait timeout produces 429 with a sane `Retry-After`, release wakes
exactly one waiter, `LinkRate` follows a fake clock.

## Task 4 — verification

1. Gates: `uv run pytest`, `uv run mypy`, `uv run ruff check` in `gateway/`, `clients/python/`,
   `datasets/`; `node --test` in `clients/js`; `java clients/java/tests/Checks.java`. The three
   client benchmarks run under those and print their medians.
2. Deploy: `git pull` + restart, then confirm `/pool` answers, a repeated row predict is a hit, and
   `/models` still lists three machines per model.
3. Load test at 50 teams, through the gateway only: 70 % row / 30 % upload, uploads at 200 KB / 1 MB /
   10 MB, ten minutes, mixed models. Measure hit rate, p50/p95 by mode, 429 count, peak
   `bytes_in_flight`, and cache size.
4. Record in `.agents/references/`: the load test against the r3 baseline
   (`2026101923_gpu-occupancy/run_*_r3`), and a separate note with the three client benchmark
   numbers, the fixture sizes, and the machine they ran on.

Acceptance:

- a repeated row predict: p50 < 20 ms, no `inference` line;
- an upload through the client: ≤150 KB on the wire for a 12 MP photo, end to end ≈ 1 s;
- the live equivalence check passes for both models: default and `resize_before_submit=False` agree within the
  tolerances above, and the resized upload is at least a third of the original's bytes;
- the three client benchmarks are under their ceilings on `labpc`, and the pass-through case is under
  10 ms;
- 50 concurrent row requests: no 429, p95 ≤ 2 s;
- 50 concurrent raw 10 MB uploads: no unbounded queue, the tail gets 429 + `Retry-After`, nobody
  waits 60 s;
- no image decoding in the gateway process (checked by `strace -c -f -p <pid>` during the run, or by
  the absence of Pillow in the gateway's dependencies).

## Not doing

- Lowering the Caddy limit; decoding or resizing on the gateway.
- Any change to the ordering score, `DELAY_PER_QUEUED_REQUEST` or `max_in_flight`.
- Hedging, batching, a second gateway region, a European GPU (the score already routes by RTT if one
  ever joins `models.json`).
- Precomputing the published row space; the cache fills from real traffic first.

## Risks

- **A cache hit hides a dead backend.** `/pool` and `/models` still show the pool; that is the
  operator's surface, not the participant's.
- **SQLite on two cores.** WAL plus one connection per thread; the hit path is a read of at most
  12 KB. If the file ever becomes a bottleneck, the cap and the prune are the knobs.
- **Queue wait against client timeouts.** The wait is capped at 20 s and `Retry-After` is explicit;
  the notebook cell should print it rather than traceback.
- **Three JS files.** The core holds no image code by design; the two entries are the only places
  that know about a platform.
