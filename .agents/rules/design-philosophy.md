# Design Philosophy

How to decide *what* to build. This applies to every change in this repository; the
repo-wide invariants it does not replace are in [`project.md`](project.md).

## Confirm the need before adding

Before implementing a feature or a safeguard, answer three questions — in the PR, or in
`.agents/references/` when the reasoning is worth keeping:

1. **What failure does this prevent, and has it been observed?** Name the specific failure.
   "It could happen" is not an observation.
2. **What is the simplest thing that prevents it?** Reduce to first principles: what is the
   actual requirement, stripped of habit and convention? Prefer the fewest moving parts.
3. **Which existing implementation already does this?** Search this repository, its
   dependencies, and the platform *before* writing anything.

Delete before you add. A safeguard that no observed failure justifies is a liability: it
costs code, runtime, maintenance, and — worst — a second source of truth that must stay
consistent with the first.

## Worked example: is running sha256 necessary?

Hashing every image is the canonical safeguard that feels mandatory and often is not. Ask
what the hash is *for*:

| Stated purpose | Survives first principles? |
|---|---|
| Verify an upload landed intact | No — S3 returns `ETag`, which for a single-part `PUT` **is the content MD5**, computed server-side. Compare local size + MD5 against remote size + `ETag`; no client hashing. |
| Skip unchanged objects on a re-run (idempotency) | No — the same size + `ETag` comparison answers it. |
| Cache key | No — CloudFront keys on the URL, not on content. |
| Deduplicate images within a dataset | No — byte-duplicate rows are a source-data-quality matter, and byte-identical objects are findable by size + `ETag` after upload without hashing. |
| Integrity of third-party weights | Maybe — prefer the platform's native checksum (S3 additional checksums, an upstream-published digest) over one we compute and store ourselves. |

Cost to weigh against those: a full read pass over the data (tens of GB, I/O bound), a stored
64-hex field per row, and one more value that must not drift from the bytes it describes.

**Rule of thumb:** when the platform already returns an integrity token (`ETag`, native
checksums), or the object key already encodes identity, do not compute a second one.
Duplicated facts drift.

## Reuse before you build

- Search the repository and the dependency set first; a dependency that already solves the
  problem beats a new module. Two implementations of the same mapping must not coexist.
- Audit an existing upstream script before rewriting it. The sample repository already ships
  `Darwin_to_hf.py`; the dataset publisher must extend that conversion path, not fork a second
  metadata mapping beside it.
- Prefer the platform primitive over a reimplementation: a public object URL over a proxy,
  `ETag` over a hand-rolled checksum, the framework's validator over a bespoke one.
