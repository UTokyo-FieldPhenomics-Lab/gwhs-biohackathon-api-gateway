# Models Rules

`models/` links a private repository; these are requirements on that repository, not a claim its
contents were inspected in an uninitialised clone. Shared constraints are in [`project.md`](project.md).

- **Keep containers, model code, weights and their access links private; publish only the contract.**
  Participants need nothing under `models/`. The submodule's entry point must link this repository's
  AGENTS/rules rather than copy them. [Split](../references/2026101605_models-submodule-and-weight-distribution.md).
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
- **A counting model answers a count and the field it integrates, never invented points.** It has no
  mask, so its `objects` is an empty list rather than boxes or dots manufactured from a density map;
  `density` is that map, cell by cell, and its sum is the count. A point set belongs to a
  point-counting model that emits its own points; nothing derives them from a field.
  [Counting contract](../plans/2026103015_✅density-field-instead-of-points.md).
