# Design Philosophy

How to decide *what* to build. This applies to every change in this repository; the repo-wide
invariants it does not replace are in [`project.md`](project.md).

## Confirm the need before adding

Before implementing a feature or a safeguard, answer three questions — in the PR, or in
`.agents/references/` when the reasoning is worth keeping:

1. **What failure does this prevent, and has it been observed?** Name the specific failure.
   "It could happen" is not an observation.
2. **What is the simplest thing that prevents it?** Reduce to first principles: what is the actual
   requirement, stripped of habit and convention? Prefer the fewest moving parts.
3. **Which existing implementation already does this?** Search this repository, its dependencies, and
   the platform *before* writing anything.

Delete before you add. A safeguard that no observed failure justifies is a liability: it costs code,
runtime, maintenance, and — worst — a second source of truth that must stay consistent with the first.

## Worked example: is running sha256 necessary?

Hashing every image is the canonical safeguard that feels mandatory and often is not. Every purpose it
is usually given is already served here: upload integrity and re-run idempotency by the `ETag` a
single-part `PUT` returns (the content MD5, computed server-side) compared against what a `HEAD`
reports; cache keys by the URL, since CloudFront does not key on content; and duplicate detection by
size + `ETag` after upload. Third-party weights, if they ever arrive, take the platform's native
checksum or an upstream-published digest rather than one we compute and store.

Cost to weigh against those: a full read pass over the data (tens of GB, I/O bound), a stored 64-hex
field per row, and one more value that must not drift from the bytes it describes.

**Rule of thumb:** when the platform already returns an integrity token (`ETag`, native checksums), or
the object key already encodes identity, do not compute a second one. Duplicated facts drift.

## Reuse before you build

- Search the repository and the dependency set first; a dependency that already solves the problem
  beats a new module. Two implementations of the same mapping must not coexist.
- Audit an existing upstream script before rewriting it; where an upstream ships one, extend that path
  rather than forking a second mapping beside it.
- Prefer the platform primitive over a reimplementation: a public object URL over a proxy, `ETag` over
  a hand-rolled checksum, the framework's validator over a bespoke one.
