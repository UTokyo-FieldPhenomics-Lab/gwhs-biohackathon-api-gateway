# WeVisOne-VL-4B in the pool

The mask spike of [`2026110521_wevisone-vl-masks.md`](2026110521_wevisone-vl-masks.md) became a model id:
a container in `models/wevisone/`, one backend on the DGX's card 1, an entry in the gateway's
`models.json`, a page on the site, and a demo that can be clicked. This is what that took and what it
cost.

## The container

`models/wevisone/` is `sam31_official/`'s shape: `vendor/wevisone_vl/` copied at upstream commit
`9a6bcd85` with `LICENSE` and `UPSTREAM_COMMIT` beside it, and `serve.py`, `outputs.py`, `infer.py` on
top. Two things in it are specific to this release:

- **The float16 repair is in `infer.py`, not in `vendor/`.** `keep_frequencies_bounded()` replaces
  `FourierPE1D.forward` so the frequency table is rebuilt in float32 at use — the same choice
  `sam31_official/processor.py` makes when it adds a method per call rather than editing the snapshot.
  The snapshot is what upstream published; the compatibility layer is where we differ.
- **`max_instances` is a declared parameter** (1–100, default 64) because it is the latency: six
  generated tokens per instance, about twelve tokens a second on a V100. `response_timeout_seconds` is
  90 and `MAX_PIXELS` is 4,000,000 — the pixel decoder returns one float mask per instance at the
  caller's size, so a 12 MP photograph with 40 heads would be two gigabytes of masks inside the
  container, while the vision encoder never looks at more than about two.

`torch` is pinned at 2.10.0 from the cu128 index — the newest build whose `get_arch_list()` still
carries `sm_70` — and `torchvision` 0.25.0 is there for a reason worth recording: nothing we wrote
imports it, but Qwen3-VL's processor imports its video processor, and without torchvision the
container dies at load with `Qwen3VLVideoProcessor requires the Torchvision library`.

## The build

`docker build` on the DGX. Weights are 9.7 GB and the first attempt to fetch them stalled at 8.1 GB
with the socket open and no bytes moving for six minutes; a ten-second sample of `tx_bytes` read
57 KB/s, and measuring over a minute read **76 MB/s** — the same sampling mistake as the ECR push in
[`20261027_aws-gpu-backend.md`](20261027_aws-gpu-backend.md), made again.

The fix is in the folder rather than in the retry: `weights/` in the repository holds only a README, and
the Dockerfile copies it to `/weights/wevisone-vl-4b` before running `fetch_weights.py`. A machine that
already has the pinned bytes — this DGX does, from the spike — puts them in `weights/` and the build
copies instead of downloading; the two byte counts are asserted either way. Seeding from
`/ssdraid0/bench/hf` took a minute.

Two images came out of it, and the first was wrong: `COPY weights /tmp/weights-seed` left the seed in
the final image, so 9.1 GB of weights appeared twice and the image was **30.4 GB**. Copying straight to
the destination and letting the fetch verify (or fill in what is missing) gives **20.7 GB**, which is
what runs.

## What is deployed

| | |
|---|---|
| Image | `wevisone-vl-4b:20261105b`, 20.7 GB, on the DGX |
| Container | `--gpus device=1`, `-p 12720:12711`, `WEVISONE_MACHINE=dgx-v100` |
| Declaration | id `wevisone-vl-4b`, `resolution: 1024`, `response_timeout_seconds: 90`, `prompt` + `max_instances` |
| `models.json` | one URL, `dgx-v100:12720`, resolved in `.agents/local/pool.md`; family `WeVisOne-VL`, label `4B` |

The container reports `budget ok: needs 15.2 GB, budget 31.0 GB`, loads in **9.6 s** and warms in
2.3 s. The warm request found **0 instances on a blank frame**, so the decoder's first launch is not
warmed — the first real caller pays about three seconds more than the model itself. That is written
down rather than papered over.

Through the gateway, `POST /models/wevisone-vl-4b/predict?prompt=wheat head` on `gwhd` row 4 answered
**200 in 24.45 s, 14,452 bytes, 43 objects**, `resolution_used: 1024`, `computed_by.machine: dgx-v100`,
`dtype: float16`. Five of those masks were decoded with `clients/python/global_wheat.py`'s own
`rle_decode` and every one summed to the published `area` exactly, which is the check that a
participant's decoder reads this answer.

A second identical call answered in **9 ms** with `cached: true` — from the gateway's cache, no lane,
original `computed_by` preserved. The demo page sends `max_instances` as well, so its call is a
different canonical query and misses that key; that is the cache behaving as the rule says, not a fault.

## The demo

Three changes in `play.js`:

- **A clock.** `running… 12 s`, ticked once a second, cleared first thing in `finally` so a tick cannot
  overwrite an error. A half-minute request with a frozen line reads as a page that has stopped.
- **The preset.** Two chips — `wheat head` on `gwhd` row 4, and `carrot` on the demo's own carrot
  photograph — because an open-vocabulary model is shown by changing the phrase, and a legend,
  because the phrase and the areas are what the answer is read for. The first version's second chip
  was `leaf`, and the page's class-colour invariant refused it: a dataset's class name may not be
  written into the page.
- **The phrase memory.** A text control survives a change of model, and it is kept in `localStorage`
  so a reload does not cost it either; every other control starts from the declaration. Today that is
  one field, `prompt`, on every model that has one. It began as a per-id map of every text parameter,
  and that was too much: a confidence threshold or a box list means something different to each model
  that declares one, and the reader cannot see which values were carried. It is keyed on the type and
  not on the name, because naming a parameter in the page is the invariant that says the page is built
  from `GET /models` — the test caught the literal within a comment, which is how the rule found out.
  Verified by typing `carrot` on `wevisone-vl-4b`, reloading the page onto `sam3.1-meta`, and watching
  a frame that answers 18 heads on its own default answer **0 objects**.

Verified in the browser rather than by reading: the page lists the id, the two chips, a textarea for
`prompt` and a slider for `max_instances`; pressing the chip then Run showed `running… 12 s`, then
`43 objects · Σ mask area = 105,457 px`, with the legend naming `dgx-v100`, the V100, CUDA 12.8 and
torch 2.10.0+cu128.

## Not done

- **The docs build was not run.** The page, the generated parameter table, the index row and the nav
  entry are written; `zensical build --strict` is the gate and has not been through it.
- **Nothing was pushed to ECR.** The rented path has no instance and no launch template, so a
  `20261105` digest for this model would be a row nothing reads. When the L4 is next wanted, the image
  has to be rebuilt in-region anyway.
- **One backend.** A second container on card 2 is one `docker run` and one `models.json` line, and
  nothing measured yet says the demo needs it.
- **`lab-intel3090` is still on the pre-dtype images** — unrelated, still waiting for someone to sign
  in to that Windows session.
