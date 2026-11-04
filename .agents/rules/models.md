# Models Rules

`models/` links a private repository; these are requirements on that repository, not a claim its
contents were inspected in an uninitialised clone. Shared constraints are in [`project.md`](project.md).
It holds two kinds of model service: a **GPU container**, and a **managed-API wrapper** that calls a
hosted model with its own credential and budget. Both are private implementations of an id the
public contract publishes; neither is gateway code.

- **Keep implementations, model code, weights and their access links private; publish only the
  contract.** Participants need nothing under `models/`. The submodule's entry point must link this
  repository's AGENTS/rules rather than copy them.
  [Split](../references/2026101605_models-submodule-and-weight-distribution.md).
- **A managed-API wrapper is a model service, not gateway code.** It runs as its own container on the
  gateway host, answers the same declaration and the same three routes, and is reached on
  `127.0.0.1` with the backend credential. It holds no weights. Its service account is least
  privilege — `roles/aiplatform.user`, or a custom role holding only `aiplatform.endpoints.predict`
  — never an owner credential, and its key is a read-only file mount, never an image layer or an
  environment value. [Runbook](../../deploy/nanobanana.md).
- **Keep the spend gate inside the wrapper.** A hosted model costs money per call, so the team
  allowance, the daily breaker and the pot floor are the wrapper's: it is the thing that spends and
  the only place that knows a price. The gateway forwards the authenticated team id and nothing
  else. [Budget](../../deploy/nanobanana.md#7-the-money).
- **Declare the response budget the model needs.** A hosted model that takes 15–40 s declares
  `response_timeout_seconds`; the gateway's ten-second default is for a segmentation answer and is
  not raised globally. The declaration is capped at three minutes.
  [Gateway rules](gateway.md#routing-and-resources).
- **Keep `.gitmodules` at `update = none`, and build/test with the submodule empty.** Public and fork
  builds cannot authenticate to it; no parent build may import, read or generate from it.
  [Configuration](../../.gitmodules).
- **Publish submodule commits before recording their pointer here.** Switch to main before the first
  edit (`git -C models switch main`) rather than committing detached; otherwise clones may reference
  an unreachable commit. Bump the pointer when the public model contract/revision changes, not for
  every private commit. [Submodule handling](../references/2026101605_models-submodule-and-weight-distribution.md).
- **Update explicitly with `git submodule update --remote --checkout models`.** `--checkout`
  overrides `update = none`; `--remote` alone does not. [Configuration](../../.gitmodules).
- **Never commit weight files in either repository.** The submodule must ignore their extensions;
  a large binary in history breaks cloning and existing pointers even if later removed.
  [Weight distribution](../references/2026101620_weights-fetched-inside-the-build.md).
- **A shared-by-link weight store is a credential.** Its link stays only in the private submodule.
  A Dockerfile ARG default and build-time download are permitted only while images remain private
  to backends and the link is revocable; public images require another delivery design.
  [Build decision](../references/2026101620_weights-fetched-inside-the-build.md).
- **Record weight identity through the supplied file id and asserted byte count, not a local hash.**
  Freeze containers before the event; identify distributed images by registry digest, or repeated
  source builds by submodule commit plus the asserted file identity. This is the recorded exception
  to requiring a versioned weight-store revision. [Weight decision](../references/2026101620_weights-fetched-inside-the-build.md).
- **Two checkpoints mean two model ids, never a request `variant`.** Checkpoint selection changes
  admitted memory; environment-selected builds/containers may share code but not a declaration for
  distinct weight sets. [Semantic baseline](../plans/2026101813_gwfss-segformer-model.md).
- **Semantic models return one object per nonempty foreground class, with an extent box.** Do not
  invent instances or background objects; participant examples may demonstrate post-processing.
  [Semantic output](../plans/2026101813_gwfss-segformer-model.md).
- **Return the shared object shape with COCO compressed RLE, never another mask encoding.** Normalize
  inside the container so one client decoder handles every model and dataset surface.
  [Gateway contract](gateway.md#data-and-model-contract).
- **Choose the half-precision by the card and publish which one ran.** `bfloat16` on compute
  capability 8.0 and above, `float16` below it, and the choice goes in `computed_by.dtype` — a V100
  has no bf16 hardware, and the two formats are the same two bytes, so the declaration's resident
  figure holds either way. Set it from the capability rather than a constant, with an env override
  for the case a container has to be pinned. [Measurement](../references/2026110418_dgx-station-v100-reachable.md).
- **A model whose forward pass demands bfloat16 is Ampere-only.** `sam3.1-meta` runs inside
  `torch.autocast(dtype=bfloat16)` and its own fused operators cast to it, so it cannot be served
  on a V100 at all — in this pool that rules out the DGX Station, and leaves the RTX cards and the
  rented L4. Changing that is a port of the operator path, not a dtype argument.
  [Capability](../references/2026110418_dgx-station-v100-reachable.md).
- **A counting model answers a count and the field it integrates, never invented points.** It has no
  mask, so its `objects` is an empty list rather than boxes or dots manufactured from a density map;
  `density` is that map, cell by cell, and its sum is the count. A point set belongs to a
  point-counting model that emits its own points; nothing derives them from a field.
  [Counting contract](../plans/2026103015_✅density-field-instead-of-points.md).
- **A backbone publishes its own tokens, never a head's output.** DINOv3 has no class list, box or
  mask, so it answers `embedding` — the CLS vector — and `features` — the patch grid as base64 of
  little-endian float16, row-major and channels-last — with `objects` empty so one client parses
  every model. A head that turns those numbers into masks belongs to whoever fits it.
  [Feature grids](../references/2026110215_dinov3-backbones.md).
- **A served model carries its license notice.** DINOv3's license requires a copy of the agreement
  and a prominent “Built with DINOv3” notice when the materials are made available to a third party;
  the models index and the backbone page display the notice and link the canonical terms. A model
  with an attribution clause is not served until its page states the clause and links the terms.
  A hosted model's page says that it is served through the provider and links the provider's terms;
  the weights are not ours to distribute, and the page must not imply otherwise.
  [DINOv3 License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/).
- **A model that answers in prose or pixels still answers with `objects: []`.** It publishes `answer`
  and `image` beside the empty list, so one client parses every model. The picture is bounded to what
  the card was measured to carry — an edit of a 3456×5184 frame will otherwise ask the decoder for
  17.9 megapixels and die — and the bound is imposed before the model runs, with the answer saying so.
  [Measurement](../references/2026110219_internvl-u-prose-and-picture-answers.md).
