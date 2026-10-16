# The demo models measured: what one request costs and what a card can carry

Date: 2026-10-16 12:36 JST, measured 12:20–12:35 on `crest-nerv`.
Topic: the three configurations the compute-plane plan's Task 1 asks for, measured on the weights the
container will actually load. Records the pre-meeting demo's latency and its resident-VRAM floor, the
concurrency ladder, and a first look at the laboratory 5090 as the host for the demo container. Two
earlier beliefs are corrected below.

## What was measured, and with what

The foldio360 masking package (`packages/masking`, its own `.venv`: Python 3.12.12, torch
2.12.1+cu130, ultralytics 8.4.89, `segmentation-refinement` 0.6) run directly against the four
checkpoints pulled from the weight store into `/tmp/opencode/weights`. Nothing was reimplemented:
the harness calls `Sam3TextMasker`, `CascadePspRefiner` and `MaskingPipeline` as they are.

- GPU: RTX 3090, 24,078 MiB visible, driver 610.57.04. **This machine is `labpc` itself** — hostname
  `crest-nerv`, `192.168.250.228`, tailnet `nerv-linux` — so these are a pool member's own numbers and
  need no translating into a placement decision. Admitted against the **23 GB budget** of a 24 GB card
  (Decision 8's 1 GB reserve), per [`../../deploy/gpu-backends.md`](../../deploy/gpu-backends.md).
- Image: `packages/masking/tests/images/wheat head/DSC_001_20240619_154505_128521.jpg`, 3456×5184 —
  the demo's own domain and the resolution the pipeline is developed against.
- Prompt: `wheat head`. SAM 3.1 `imgsz=1036`, CascadePSP `L=900`, `crop_expand=1.20`.
- **One process per configuration**, so a resident figure is never contaminated by the previous
  configuration's allocator.
- Harness: `/tmp/opencode/harness/measure.py`, one JSON record per run, results in
  `/tmp/opencode/harness/out/*.jsonl`. Rerun with
  `<foldio360>/.venv/bin/python /tmp/opencode/harness/measure.py --config <c> --concurrency <n>`.

## One request, single-threaded

| Configuration | Construct | First call (loads weights) | Resident, process | Resident, torch allocated | Peak, process | Warm latency | rps |
|---|---|---|---|---|---|---|---|
| SAM 3.1 semantic (`imgsz` 1036) | 0.19 s | **7.12 s** | 4,914 MiB | 2,009 MiB | 5,382 MiB | **0.391 s** | 2.55 |
| CascadePSP (`L` 900, crop 1244²) | 0.89 s | 2.15 s | 5,248 MiB | 259 MiB | 5,250 MiB | **1.442 s** | 0.69 |
| **Pipeline (SAM 3.1 → CascadePSP)** | 0.19 s | **9.16 s** | **8,474 MiB** | 2,269 MiB | 8,486 MiB | **1.965 s** | 0.51 |

The pipeline row is the one the pre-meeting demo calls, and it is the sum of its two stages:
0.391 + 1.442 = 1.833 s against a measured 1.965 s, the difference being the crop, pad and paste
around the refine.

## The concurrency ladder

| Configuration | Throughput c=1 | c=2 | c=4 | Latency c=1 → c=4 | Process peak c=1 → c=4 |
|---|---|---|---|---|---|
| CascadePSP | 0.693 | 0.695 | 0.694 | 1.442 → 5.762 s | 5,250 → 13,286 MiB |
| SAM 3.1 semantic | 2.553 | — | 3.709 | 0.391 → 0.995 s | 5,382 → 5,418 MiB |
| Pipeline | 0.509 | 0.561 | — | 1.965 → 3.429 s | 8,486 → 12,346 MiB |

**CascadePSP saturates the card on one request.** Throughput is flat to three digits across the
ladder while latency grows exactly linearly (4 × 1.442 = 5.77 against a measured 5.762) and memory
grows linearly at **+2,679 MiB per additional in-flight request**. Four in-flight requests therefore
buy nothing and cost 8 GB; the worth-having `max_in_flight` is 1, and extra capacity means extra
cards. The pipeline inherits this, since CascadePSP is 73 % of its latency.

**SAM 3.1 does not saturate it** — 4-way concurrency gained 45 % throughput. Treat that as a
question, not a result: the Ultralytics predictor keeps the current image on the instance, so two
threads sharing one predictor can interleave a `set_image` with another request's `predict`. The
timings are real; whether those four answers were correct was not checked, and the container's
the predict route must not reuse one predictor across requests without a lock or per-request state.

## Two corrections to what this project believed

- **The pipeline holds both models; it does not stage them.** `MaskingPipeline` caches its localizer
  and its refiner and releases neither. The evidence is exact rather than inferred: torch's
  allocated memory for the pipeline is 2,269 MiB, and the two stages' own figures are 2,009 + 259 =
  2,268 MiB. So the resident floor for the demo model is **8.5 GB of process VRAM, not the larger
  stage's 5.2 GB**, and this is the number to admit a container against.
- **A request is 1.97 s warm, not the 3.1 s an earlier reading of the August gate suggested.** That
  gate measured CascadePSP on its own crops across 17 images; this is the pipeline on one wheat-head
  frame at 3456×5184. Same artifact (the store's `cascade_psp` hashes to the digest that gate
  validated), different input.

## What this gives the demo on 20 October

- **A call returns in ~2 s once the container is warm, and ~9 s cold**, because the first call pays
  for loading both checkpoints. The container must be pre-warmed before the meeting: send one
  throwaway request at start-up and keep the process alive, or the first live call is 9 s.
- **One 3090 serves ~1,800 pipeline calls an hour** (0.509 rps). The on-prem pair — home 4090 and
  laboratory 5090 — is therefore roughly 4–5 thousand an hour, all of it with no rental.
- **8.5 GB resident means every card in the pool fits it** against its own budget (23 GB of a 24 GB
  card, 31 GB of the 32 GB card), including the 12 GiB floor the foldio360 README states, so no card is
  chosen for fit. Renting buys throughput and contracted availability, which is what the plan already
  says and now has numbers behind.

## The laboratory 5090 as the host

Checked 2026-10-16 over SSH, read-only except one `docker pull`:

- Online; Docker engine `overlayfs`, 24 CPUs, 67.1 GB visible to the engine; **168.6 GB free of
  930.4 GB on `C:`** — ample for a CUDA torch image.
- The GPU still reaches a container: `nvidia/cuda:12.4.0-base-ubuntu22.04` reports
  `NVIDIA GeForce RTX 5090, 32607 MiB, 610.88`.
- **`docker pull` fails from an SSH session**: `error getting credentials - err: exit status 1, out:
  A specified logon session does not exist. It may already have been terminated.` Docker Desktop's
  credential helper needs an interactive logon session the SSH session does not have, so it cannot
  answer for anonymous pulls either. Two ways out, and the second is better: turn the credential
  helper off for this machine, or **build on the workstation and `docker save | docker load`
  across**, which needs no registry, no Docker Hub account and no internet on the 5090 at all — the
  weights are already baked into the image by then, per [`../rules/models.md`](../rules/models.md).

## Still open

- The same three configurations on the **5090** itself, which is where the demo will run.
- Whether SAM 3.1 at concurrency above one returns correct masks, or only fast ones (above).
- **What the refine stage's crop side does on a subject that fills the frame** — the one figure that
  could invalidate the 8.5 GB declaration. The refine stage's input is the crop the rough mask defines,
  so its side follows the object (1244² here) and CascadePSP's working set scales with crop area, not
  with the input image. A large subject on a 3456×5184 frame could ask for several times the memory
  measured, and nothing in the pipeline caps it. A sweep across crop sides is needed before `resident_gb`
  8.5 is declared, and it is the strongest argument for the container imposing its own fixed refine
  input rather than inheriting the object's size.
- The pipeline on the other seven prompt classes: one image of one class is the whole sample so far.
