# The model API surface: the contract, the naming, and the selector

Status: **surface implemented and rehearsed; three steps still open** (2026-10-16). The four routes,
the id, the discovery endpoint and the selector are in `gateway/src/biohack_gateway/models.py`; the
container answers the same paths and declares its own parameters. 131 tests pass, `ruff` and
`mypy --strict` are clean, and the end-to-end rehearsal (gateway → container → real SAM 3.1) returns
a result identical to calling the container directly. Two of the open steps are handed to
[`2026101617_play-page.md`](2026101617_play-page.md), and the notes below are the moved and
translated discussion draft that decided all of it.

Owner files: `gateway/src/biohack_gateway/models.py`, `gateway/src/biohack_gateway/app.py`,
`gateway/src/biohack_gateway/datasets.py` (the shared `ObjectOut`),
`models/sam31_official/serve.py`, `models/sam31_official/outputs.py`.

## Goal and acceptance

Let a participant run a segmentation model on one image with one key-authenticated call, without
deploying the model or owning a GPU, and without the gateway ever learning what a mask is.

Acceptance:

- **A new model is added by configuration alone** — no gateway code change, no special-casing of a
  model or a machine. The model declares what it accepts; the gateway repeats the declaration and
  forwards the values verbatim.
- **One object shape across both surfaces.** A participant writes one decoder and reads both a
  dataset's geometry and a model's output with it.
- **A dead backend costs one timeout**, and the request still succeeds on the next one.
- **A client-supplied URL is never fetched.** The public surface does not accept one at all.

## Settled, not reopened

| Settled | What |
|---|---|
| Four routes | `GET /models`, `GET /models/{model}`, `POST /models/{model}/predict` (JSON), `POST /models/{model}/predict/upload` (bytes) |
| Response shape | `{model, objects: [{idx, category, bbox, score, area, rle}], resolution_used}` |
| Authentication | Participant `X-API-Key`; gateway→backend `X-Backend-Token`; **a participant's key never reaches a backend** |
| Error mapping | `4xx` is the request's fault and is not retried; `5xx` or a timeout is the backend's fault — mark it unhealthy for 30 s and try the next |
| One round trip | No streaming and no multi-step handshake. `/health` is read once at startup for what a model serves and probed every 15 s for whether it is there (2026-10-17); a prediction is still one round trip |
| Normalisation in the container | The gateway has **no ML dependency** and never touches geometry |

## Geography, corrected

An earlier draft had the data and the participants on the same side of the world. They are not:

- The data (the S3 origin) is in **Frankfurt**.
- Most participants are **in Germany**, some in Australia and the Americas.
- The on-prem compute is in **Tokyo** (labpc's 3090, a 5090); rented compute could sit in
  `eu-central-1`.

Three consequences:

1. **Image bytes cross the WAN, but not on every call.** A backend fetches the image from
   CloudFront; an edge hit stays in-region and only a miss pulls the object from Frankfurt over
   CloudFront's own backbone. The objects are `immutable` with a one-year TTL, and a warm fetch
   answers `x-cache: Hit` — both observed on 2026-10-16.
2. **The gateway in Frankfurt is right.** It sits beside the data and near most participants. What
   is far away is the compute.
3. **Renting in `eu-central-1` is repriced.** It is not only a third fault domain, it is the
   low-latency path for most participants: for a German participant a Frankfurt backend removes
   roughly **0.5–0.7 s** of trans-Pacific control round trip, against **0.36 s** of inference. That
   moves "should we rent" from availability to availability plus latency for the main audience.

## The naming rule is worth more than the name

**An id names whose weights these are — not the code path, and not the deployment role.**

- The Meta checkpoint is **`sam3.1-meta`**. It reads as "Meta's weights, not ours" and carries no
  lifecycle, so it stays true whether or not this container ships.
- Rejected: `-raw` (reads as "unprocessed output"), `-origin` (a git term), **`-demo` (a deployment
  role — and the original may never go live, which is exactly why lifecycle does not belong in an
  id)**.
- A collaborator's fine-tune is named after **its training data** (`sam3.1-wheat`), not `-ft`: a
  participant should be able to read what a model can segment out of its id.
- **A variant gets its own id, not a parameter on an existing one.** Adding the CascadePSP refine
  later takes residency from 3.7 GB to about 8.5 GB, and `resident_gb` is declared per id — as a
  parameter it would make admission impossible to compute in advance.

## The discovery endpoint

Public, behind the existing `X-API-Key`. It answers two questions at once: **what can I call** (the
only place a participant discovers an id) and **is it up** (so nobody waits for a timeout to learn
otherwise).

```json
{ "models": [ { "id": "sam3.1-meta", "resolution": 1008, "backends_up": 1, "max_in_flight": 1,
                "checked_seconds_ago": 3,
                "machines": [ { "machine": "lab-3090", "gpu": "NVIDIA GeForce RTX 3090",
                                "compute_capability": "8.6", "cuda": "12.8", "torch": "…" } ],
                "parameters": [ { "name": "prompt", "default": "wheat head", ... },
                                { "name": "confidence_threshold", "default": 0.5,
                                  "minimum": 0.0, "maximum": 1.0, ... } ] } ] }
```

`backends_up: 0` means no backend is answering right now.

It is **not** an operator dashboard: no addresses, no VRAM, no queue depth, no logs.

## Parameters belong to the model

- **`prompt` is open**, because that is the whole point of a text-prompted model: the participant
  brings their own word. Its default is published with the model.
- **`confidence_threshold` is optional**, with a published default of `0.5` and a range of `0`–`1`;
  out of range is a `400` from the container.
- **No `imgsz`, and no client-set resolution.** Resolution is the container's property: a
  client-set one would make the memory peak a variable, and admission could no longer be computed
  in advance.
  > **Superseded 2026-10-18** by
  > [`2026101811_model-parameter-contract.md`](2026101811_model-parameter-contract.md): `imgsz` is
  > exposed as a measured allow-list under a ceiling the container clamps to and reports. A size
  > *below* the ceiling cannot raise the peak, which is what the objection was about; the ceiling is
  > unchanged and admission is still computed in advance.
- **The parameters are declared by the model at `/health`**, repeated by `GET /models`, and
  forwarded verbatim as query parameters. The gateway adds none of its own and interprets none.

## Response conventions, fixed here

- **`bbox` is COCO's `[x, y, width, height]`**, not `[x0, y0, x1, y1]`. Not a preference: the
  dataset surface publishes XYWH, and one decoder across both surfaces is the rule for this package.
- **`bbox` is the model's own box, not the mask's tight bounding box.** They are separate
  predictions and they do not agree exactly; a reader who measures the mask and finds slack in the
  box is looking at the contract working as declared, and the documentation has to say so.
- **`area` is published although nothing requires it**, because the documentation site's own example
  uses "the decoded pixel count equals `area`" as the cheapest check that a decoder is right.
- **RLE only, never image bytes.** Beyond "the gateway carries no bytes", a browser decoding the RLE
  on the spot proves the contract was used correctly — and the site already ships the decoder in
  three languages.
- **`resolution_used` is kept**, and it is the field that explains a mask's blockiness: the
  processor resizes the image to a square without preserving aspect ratio, so a mask computed at
  1008 and mapped back onto a 2509-pixel-tall image is upscaled by about 2.5× and its boundary is
  visibly coarse at full size. That is the resolution, not the model.

## The selector, not a scheduler

The minimum is four things: **a per-model backend list → pick one that is not passed over → on
failure mark it unhealthy and try the next → carry the model's declared `max_in_flight`**. No queue,
no priority, no preemption, no cross-backend orchestration, no geographic routing.

This is a measurement, not a taste: with `max_in_flight = 1` and 0.36 s per inference on one card
(about 2.8 rps), one machine carries the demo and probably the event. What is scarce is throughput,
not concurrency — and more throughput means more cards, not more in-flight requests.

## Deferred

- **An operator dashboard.** `/health`, the logs and `docker ps` are enough for two operators; the
  event week is soon enough to decide.
- **Geographic routing.** It needs a Frankfurt backend to exist first.
- **Several prompts in one request.** The visual encoding is prompt-independent, so a shared
  `backbone_out` would make it cheap; a later optimisation.

## Measured facts

The container (`models/sam31_official`, official code and weights, a 3090), on a 5184×3456 image:

| Item | Value |
|---|---|
| Model load | 8.24 s (`/health` answers `503` until it is warm) |
| One inference | **0.36 s** |
| Resident / after a request | 3812 MiB / 5644 MiB (declared 3.7 + 1.8 GB) |
| Output | 1 object, `bbox [2321.0, 1876.7, 766.1, 766.7]`, `score 0.582`, `area 259818`, `rle.size [3456, 5184]` |
| Conformance | RLE **byte-identical to pycocotools**; round-tripped through the site's decoder with the pixel count equal to `area` |

What the same model does with the **published** rows at 1008 — measured 2026-10-16, and the input to
choosing any example the documentation shows:

| Image | Prompt | Result |
|---|---|---|
| `gwhs-temp` row 0, 592×2509, one spike on a dark ground | `wheat head` (the default) | **1 object, score 0.520**, `area 173777`, `bbox 254×1461` — the whole head |
| `gwhs-temp` row 1 | `wheat head` | 1 object, score 0.715 |
| `gwhs-temp` rows 2–3 | `wheat head` | 0 objects |
| `gwhd` row 0, 1024×1024 field quadrat with **144 annotated heads** | `wheat head` | **0 objects at every threshold from 0.5 down to 0.05** |
| `gwhd` row 0 | `wheat` / `plant` | 99 objects at ≤0.112 / 4 at ≤0.457 — noise-level |
| `gwhd` row 1, broad-leaf vegetation | `plant` | 1 object at **0.887**, but its mask is one connected canopy region cut off by the frame with a box covering most of the image — **a blob, not a plant, and not usable as a capability example** |
| `gwfss` row 0 | `wheat head` | 0 objects — **and correct**: that row's own palette says it holds no head pixels |

Read together: at 1008 this model segments a conspicuous head in a close-up and finds none of the
small, dense heads in a field crop. The field case is **probably not a resolution problem** — a head
in `gwhd` is about 57 px in a 1024 px image, near 1:1 at 1008, and the model does return
canopy-scale regions for `wheat` and `plant` — so raising the resolution or moving to a larger card
may not change it. That is untested, and it is the measurement that decides what the 20 October
conversation can promise.

## Rejected, written down so it is not revisited

| Option | Why not |
|---|---|
| Participants call a backend directly | Backends must stay private, and a per-backend token would reach participants |
| The gateway proxies image bytes by default | It gives up the CDN and the cost model, against "image bytes never cross the gateway" |
| A client-set resolution or `imgsz` | Resolution is the container's; otherwise the memory peak is a variable and admission cannot be computed. **Superseded 2026-10-18** by [`2026101811_model-parameter-contract.md`](2026101811_model-parameter-contract.md): a size below the ceiling cannot raise the peak, so `imgsz` is exposed as a measured allow-list the container clamps to |
| Lifecycle in the id (`-demo`, `-prod`) | An id describes a model, not a deployment |
| A scheduler now (queue, priority, preemption) | One machine is already saturated; there is nothing to schedule |
| An operator dashboard now | Two operators and `/health` are enough; decide in the event week |
| A hosted endpoint (HF Inference Endpoints) as a backend | **Awaiting the meeting**: whether online compute is rented at all, whether that is AWS or HF, or whether participants are guided to run their own (they can use ZeroGPU). If it is HF, it has to answer for being publicly reachable, which `project.md` forbids for a backend — the deviation-recording precedent is §1.3 of `deploy/README.md` |

## Steps

- [x] Fix the id: `sam3.1-meta` — in the container's default, its image label, and the host's model
  list
- [x] The discovery endpoint, and the `/models/list` naming retired from both documents
- [x] Drop `imgsz` from the protocol — the public surface does not accept a URL at all now, which is
  stricter than the draft
- [x] Publish `bbox` as XYWH, plus `area` and `score` — `score` added to the shared `ObjectOut`
- [x] The selector's four things; the per-key gate **not** built, because `rules/gateway.md` holds it
  as the deliberate next layer
- [x] End-to-end rehearsed: gateway → container → real SAM 3.1 over the upload route
- [ ] Measure the endpoint's latency composition, and write the trans-Pacific leg into the
  documentation
- [ ] The documentation's model page and its browser demo — **handed to
  [`2026101617_play-page.md`](2026101617_play-page.md)**. The page and the demo panel are in place;
  what is still open there is the recorded call the panel replays and the end-to-end browser
  rehearsal.
- [ ] **The meeting**: whether online compute is rented, AWS or HF, or participants are guided to
  their own (ZeroGPU)
- [ ] A new container copies the same paths and declaration format
  (`models/sam31_official/README.md`)
