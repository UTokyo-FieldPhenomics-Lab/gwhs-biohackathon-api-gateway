# The GPU compute plane: the pool, the protocol, and what a container must declare

Status: **planned, nothing implemented** (2026-10-16). The rule changes this plan owes are listed
under [Rule changes owed at implementation](#rule-changes-owed-at-implementation) and are
deliberately **not** written into `.agents/rules/` yet — each lands in the pull request that
implements the thing needing it, per the process in [`../rules/project.md`](../rules/project.md).

## Goal and acceptance

Route inference work from the gateway to GPU backends that are never publicly reachable, over one
protocol that is blind to who provides the hardware, so that a participant gets a segmentation
result for one image with one key-authenticated call and never learns where it ran.

Acceptance:

- A new backend is added, replaced, or removed by **configuration alone** — no gateway code change
  and no special-casing of a provider, a model, or a machine.
- A backend that dies costs **one timeout**, and the request still succeeds on the next one.
- A backend that cannot fit its model **refuses to start with a message naming the numbers**,
  rather than OOMing on the first inference.
- Every model has at least **two independent fault domains** behind it, or the plan records why not.

## Measured facts this plan rests on

Numbers this plan is built from, with where each came from. The first task exists because two of
them are still other people's measurements.

- **SAM 3 is 848M parameters and a 3.45 GB checkpoint** (Meta; Ultralytics lists 3450 MB).
  **SAM 3.1** (2026-03-27) is a drop-in checkpoint whose addition, Object Multiplex, targets
  multi-object video; **the image detector and the interactive point-prompt head are unchanged**, so
  the image path's memory is SAM 3's.
- **Single image at 1024px in FP16 is ~10–12 GB total** — 8–10 GB for the image encoder, plus the
  prompt encoder and mask decoder (third-party deployment guide). The same source gives batch 8 →
  22 GB, batch 32 → 40+ GB, video → 28 GB+; another puts SAM 3 as fitting "comfortably on 16 GB
  GPUs". **Third-party, and to be replaced by our own measurement** — see Task 1.
- **Cards on hand: 5090 = 32 GB, 4090 = 24 GB, 3090 = 24 GB.** One 5090 in the lab, a second
  borrowable.
- **Both on-prem candidates already answer the gateway host on their private addresses, and neither
  runs a tailscale client.** Measured 2026-10-16 from the host: `192.168.209.228` (home, 4090)
  answers TCP 22 and 22000 at **242–250 ms**; `192.168.250.228` (lab, 3090) answers TCP 2233, 8771
  and 22000 at **337–350 ms**; both at 0% ICMP loss. Each `/24` is advertised by that location's own
  GL.iNet router, so a machine joins the pool by being plugged in
  ([`../references/2026101610_backend-reachability-over-the-room-routers.md`](../references/2026101610_backend-reachability-over-the-room-routers.md)).
- **A closed port is not an unreachable host.** The laboratory machine refused 22 only because it
  runs no `sshd`, while answering on three other ports. Routing and a listening service are two
  separate questions and were measured separately.
- **Tokyo→Frankfurt is 242–350 ms**, the physical floor and the reason the protocol stays at one round
  trip per inference. The home path measured ~100 ms better than the laboratory path, and the
  laboratory path jitters by hundreds of milliseconds within a single ping run
  ([`../references/2026101610_backend-reachability-over-the-room-routers.md`](../references/2026101610_backend-reachability-over-the-room-routers.md)).
- **Rental, on demand, quoted per hour and multiplied by a 120-hour window:** 24 GB ≈ $0.80/h →
  **$96**; 48 GB ≈ $1.80/h → **$216**; 80 GB ≈ $2.50/h → **$300**; 100 GB-class ≈ $4.50/h →
  **$540**. Watch these as magnitudes, not prices.
- **AWS GPU instance families start at zero quota on a new account** and need a service quota
  increase before a single instance launches. This is a calendar risk, not a technical one.
- **ZeroGPU is not an H200 and not per-participant.** It is an RTX Pro 6000 Blackwell, half (48 GB)
  or full (96 GB); it is **Gradio SDK only**; quota is **per account per day** — 2 minutes
  unauthenticated, 5 free, 40 PRO/Team, 60 Enterprise — resetting 24 hours after first use, and
  $1 per 10 minutes beyond it.

## Decisions

1. **The protocol never learns what the hardware is.** `gateway.md` already states it: a backend is
   any Docker-capable GPU resource behind one uniform protocol, and the gateway never special-cases
   a provider. Three transports satisfy the "never publicly reachable" invariant and are invisible
   above them: a security group reference when the backend shares the gateway's VPC, the tailnet
   when it does not, and a provider tunnel. On the tailnet the backend runs no client of its own:
   each location's router advertises its `/24` and the host accepts routes, so the backend is a
   container on a machine that is already on a routed LAN. A managed serverless endpoint satisfies
   none of the three transports and is therefore not a candidate.
2. **Interchangeability is per model, not global.** A 24 GB card and a 32 GB card cannot carry the
   same set of models, so "try backends in order" applies *within* one model's list.
3. **Capability is derived, not tabulated.** Configuration holds the **set of backends** — the one
   fact that cannot be derived from anything — and each backend declares which models it serves.
   The routing table is the join. A hand-maintained machine→model table would be a second source of
   truth and would drift.
4. **The redundancy target is two independent fault domains per model**, never three replicas. Fault
   domains differ by network, power, and administrator. Machines inside one `/24` share all three —
   one router, one uplink, one power feed — so a second and third machine there add almost nothing,
   while the home machine and the laboratory machine are genuinely independent of each other. On-prem
   therefore already supplies two domains for the light models; the rented instance buys a third and a
   contracted one, not the second.
5. **What is scarce is concurrency, not fit.** One sam3.1 inference is ~12 GB, so a 24 GB card is
   filled by one request, not by the model. Every card added is a concurrency slot, which is the
   pool's first and least arguable justification.
6. **Inputs are an address or an upload, never a client URL.** The default and scaling path is
   `(dataset, row)`, resolved by lookup and handed to the backend as the public `data.*` URL.
   Uploads are kept because a participant may need to send a photograph taken on site. A
   client-supplied URL stays forbidden: parsing it is what stops the gateway becoming a proxy into
   the private network.
7. **Resolution is a property of the model, and coordinates come back in the participant's frame.**
   The container resizes to its own fixed input before inference. This makes the memory peak a
   constant whatever arrives, and — more importantly — it means the boxes and RLE are returned in
   the coordinate frame of the image the participant actually sent, so a non-square image is never
   silently stretched under the boxes they draw.
8. **One config declares the resolution and the memory, because they determine each other.**
   `input_resolution`, `resident_gb` (weights and framework, once per card), and `per_request_gb`
   (the marginal peak of one inference). Admission is `free >= resident + per_request`; concurrency
   is `1 + floor((free - resident - per_request) / per_request)`.
9. **Admission is checked when a container is placed, and the gate guarantees it at run time.**
   The deploy script is the only place that can *place* — if one card cannot take the container,
   another may — so it decides, and the container's entrypoint repeats the check as a fallback for
   a manual `docker run`. Neither substitutes for the other: a container admitted now can still be
   crowded out by one started later, and only the concurrency gate covers that.
10. **The concurrency a backend can sustain is reported, not guessed.** A backend declares
    `{model, max_in_flight}` when it starts, so the per-model gate is arithmetic on a measured
    number.
11. **A rented instance is a concurrency and contracted-availability decision, not a fit decision.**
    After the correction in Decision 8's measurements, sam3.1 fits on every card on hand, and on-prem
    already spans two independent domains, so nothing about fitting forces a rental. What the pool
    cannot supply for free is enough simultaneous slots when several teams call at once, and a domain
    whose availability is bought rather than borrowed.
12. **ZeroGPU is a participant-owned path, not pool capacity** — the pool's contract is a
    persistent, privately reachable endpoint whose latency is bounded by its own timeout, and a
    per-call shared queue behind a public hostname is none of those. It stays a legitimate thing for
    a team to do itself, which matches the position that the official API is the main path and
    self-deployment is not blocked.

## The protocol

```
POST /infer            application/json      {model, input: {dataset, row}, params?}
POST /infer/upload     multipart/form-data   (model, imgsz?, file)
```

Both routes share one response schema, one error contract, and therefore one decoder — which is what
`gateway.md` already requires across the two surfaces. They are two routes rather than one
content-type branch so each handler has one job.

```
200 {model, objects: [{bbox, category?, score?, rle?}], resolution_used?}
```

- **Authentication is per backend**: the gateway presents `X-Backend-Token`. A participant's key
  never reaches a backend, and a backend knows nothing about the token pool.
- **Error mapping is what makes the retry rule executable** — `gateway.md` says a failed backend is
  marked unhealthy for 30 seconds but does not define *failed*:

  | Backend answer | Gateway |
  |---|---|
  | `4xx` | the request's fault — no retry, answered through the gateway's own error contract |
  | `5xx`, timeout, connection failure | the backend's fault — mark unhealthy for 30 s, try the next |

- **The timeout replaces a health loop.** `gateway.md` forbids polling, so a request is how the
  gateway learns a backend is gone; the timeout is the only knob that decides what that costs. Its
  value is deliberately left open below.
- **A backend's `/health` is for a human and the deploy script, never the gateway.**
- **Normalisation happens in the container**, so the gateway adds nothing to the geometry and keeps
  zero ML dependencies.
- **One round trip per inference.** No streaming and no multi-step handshake: the on-prem backends
  sit 242–350 ms away, and a second round trip doubles a cost that is already the reason to prefer a
  nearer backend.

## Backend configuration and admission

Declared once, in the model's own directory, and published twice — as the container's config and as
an image label, so a host can read it with `docker image inspect` without starting anything:

```jsonc
{ "model": "sam3.1",
  "input_resolution": 1024,
  "vram": { "resident_gb": 4.0, "per_request_gb": 8.5 } }
```

```dockerfile
LABEL io.biohack.vram.resident_gb="4.0" io.biohack.vram.per_request_gb="8.5"
```

A test asserts the label and the file agree, so the pair cannot drift.

The deploy script, in order:

1. Read the declaration from the image.
2. Query every card — `nvidia-smi --query-gpu=index,memory.free --format=csv`.
3. **Place** the container on a card that can take it; prefer the one left with the most room.
4. If no card can, print what it needs and what is free, and continue — a skipped model is not a
   failed deploy, and the script stays idempotent.
5. Never `restart: always` on such a container, or a refused start becomes a restart loop.

`memory.free` understates what the driver could eventually reclaim, because a running container's
allocator holds what it has cached. For an admission check, being conservative is the correct
direction.

## Rule changes owed at implementation

Each is written in the pull request that implements the thing that needs it, with the reason:

- **`project.md`, the byte invariant — widen it to the read path.** It currently says bytes never
  traverse the gateway, on the stated grounds that inlining them voids the CDN and the cost model.
  An upload is a single write from a single client and voids no CDN read, so the invariant becomes:
  read path addressed and never served; one exception, an upload, capped in size, never persisted,
  and never a dataset object. The `client-supplied URL is parsed, never fetched` invariant beside it
  does **not** move.
- **`project.md`, the concurrency gate — add the per-model dimension.** It currently names a global
  and a per-key gate. A 24 GB card taking one sam3.1 and several yolo means the gate has to be
  per model as well, and the rule says to change it in the same pull request and explain why.
- **`gateway.md`, the model surface** — the shape of `/infer` and its error mapping, once written.
- **`project.md`, the overlay rule — record the deviation rather than the tag.** The rule asks for a
  tag and an access policy on the grounds that "the overlay is not assumed to be ours alone", which
  the inventory confirms: the tailnet carries `labvpn@`, `labdevices@`, `guowei@` and
  `haozhou-wang@` nodes, none tagged, under a default policy of everything-reaches-everything. A
  tailnet ACL is one file for the whole tailnet, so tightening it changes four other people's network
  policy and can break access they rely on. The decision is to accept the exposure for the event and
  write it down: the per-backend token becomes the only lock, what that leaves exposed is a peer
  reaching an inference port and bypassing the gateway's concurrency gate — not a leak, since no key
  material crosses the overlay — and the compensating control that costs nothing is already there,
  the container bound to `127.0.0.1` behind `tailscale serve`, which also keeps the port off the
  laboratory LAN. Revisit if a node from outside the project joins the tailnet, or if a backend
  outlives the event window. `deploy/README.md` §1.3 now records the deviation; `../rules/project.md`
  states the invariant and is deliberately left to this implementation, and `../rules/deploy.md` never
  stated it.

## Resource options

| Option | 120 h, magnitude | What it buys |
|---|---|---|
| Pure on-prem | $0 | Everything for yolo and SPROUT; sam3.1 in two independent domains (home and laboratory), neither of them contracted |
| 24 GB rented | ~$96 | One sam3.1 slot, a third domain, and availability that is bought instead of borrowed |
| 48 GB rented | ~$216 | Two or three sam3.1 slots, all models resident at once |
| 80 GB rented | ~$300 | Comfortable headroom on both axes |
| A managed endpoint (HF Inference Endpoints) | the same ladder, billed by the minute | Nothing new on capacity, and it buys the one thing a plain instance does not: scale-to-zero billing, which pays during rehearsal and not during the event. It is also the only candidate that is publicly reachable, which [`../rules/project.md`](../rules/project.md) forbids — see [`../references/2026101612_hf-inference-endpoints-and-a-client-package.md`](../references/2026101612_hf-inference-endpoints-and-a-client-package.md) |

Sizing order: **measure (Task 1) → decide the concurrency the demo needs → pick the card.** Renting
a card so a model fits is no longer a reason; the measurements above put sam3.1 on every card on
hand.

## Tasks, in order

1. **Measure each model** at its own container resolution, FP16, inference mode, on the weights the
   container will actually load: `resident_gb`, and the peak at concurrency 1, 2, and 4.
   `per_request_gb` is not a constant — batching amortises the encoder and concurrent streams do not
   — so the largest concurrency that fits is read off the ladder, not divided. Output: the three
   configs. **Unblocked 2026-10-16**: the weight store is live (`../rules/models.md`), holding
   SAM 3.1, SAM 2.1 HQ, CascadePSP and BiRefNet — by filename all four are the *upstream public*
   checkpoints, so the reference numbers need no code from outside this organisation and none of
   this waits on anyone else. The store's link and per-file ids are in the private submodule's
   `README.md`. Measure the models the pre-meeting demo would call first: SAM 3.1 and the CascadePSP
   refine.
2. **Define the protocol types** — the Pydantic models shared by the gateway and the container, and
   the container's server implementing `/infer` and `/infer/upload` with the geometry contract the
   public docs already describe.
3. **Bring up one real backend** over its private link and prove the link end to end with the
   cheapest model. Both on-prem candidates are already reachable, so this needs no new hardware: a
   container on `192.168.250.228` rehearses the tailnet path, and a rented 24 GB instance rehearses
   the rented path.
4. **Demonstrate failover** by stopping that backend mid-run, which is also what calibrates the
   timeout.
5. **Then** decide the rental, and ask ETH for what it can commit.

## What is open

| # | Item | Decides |
|---|---|---|
| 1 | The timeout value, and the global gate's size | What a dead backend costs a participant |
| 2 | Whether the gate gains the per-model dimension, and how it is expressed | Rule change above |
| 3 | 24 vs 48 GB rented, and whether it is rented at all | Task 1's measurements plus the demo's concurrency. 48 GB is the ceiling for a single GPU on EC2 (`g6e` = L40S); 80 GB exists only inside eight-GPU instances, so that option means a different provider |
| 4 | The ETH request: how many hosts, what memory, over which link, with whom as contact | The meeting |
| 5 | The AWS GPU service quota — requested 2026-10-16 for `L-DB2E81BA` in `eu-central-1` | Nothing, but the region and the requested value still need confirming, and approval can take days |
| 6 | Whether the second borrowed 5090 arrives | Concurrency, not redundancy: it lands inside the laboratory's existing fault domain |
| 7 | `../rules/project.md` still states the tag-and-access-policy invariant, which the event deviates from; `deploy/README.md` §1.3 records the deviation, and `../rules/deploy.md` never stated it — the plan listed that file in error | Amending the invariant lands with the implementation, per the note above |
| 8 | Whether a managed endpoint is admissible at all, since `project.md` forbids a publicly reachable backend and one is exactly that | The 20th, alongside the rental question |
| 9 | Whether the pre-meeting demo is SAM 3.1 plus the CascadePSP refine, and how far it must go — a call that returns a mask, or a call a participant can make from the site | The order of Tasks 2 and 3, and therefore what must be working by the 20th |
