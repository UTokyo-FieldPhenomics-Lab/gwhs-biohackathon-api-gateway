# Project Rules

Binding constraints for this repository. Keep them short; each rule exists because a
specific failure mode was identified. If a rule blocks real work, change the rule in the
same PR and explain why.

This file holds repo-wide invariants. How to decide what to build lives in
[`design-philosophy.md`](design-philosophy.md). Package-level constraints live in one file
each:

- [`gateway.md`](gateway.md) — gateway
- [`datasets.md`](datasets.md) — dataset publisher
- [`docs.md`](docs.md) — documentation site
- [`deploy.md`](deploy.md) — deployment

Rules state what must hold and why. Execution logs, decisions, and lessons learned belong in
`.agents/references/`, not here.

## Architecture invariants

- **Never proxy bytes.** Wrapping a static resource means answering `302` to the static
  plane. Inlining image bytes pulls bandwidth back onto the gateway and voids the CDN and
  cost model.
- **Static before dynamic.** If data can be delivered as objects, do that. Add a server
  only when a trigger for it actually exists.
- **Single source of truth.** The gateway reads `index.jsonl` from S3; it never generates
  or caches its own index.
- **Paths are immutable.** Once published, an S3 key never changes. Any attribute that can
  change after release stays in the index, not in the path.
- **`/v1` is frozen.** Only additive changes; a breaking change means a new version prefix.
- **No hashing capability.** Nothing here computes or stores a content hash or checksum.
  Identity comes from names, keys, and platform tokens (`ETag`, S3 native checksums, image
  digests, object versions, upstream revisions). A content hash returns only against a
  measured failure.

## Security

- **No secrets, ever, including git history.** Bucket names, account IDs, and keys arrive
  via SSM or environment variables, never in the repository.
- **Validate `image_id` against the index allowlist.** This is the most likely real
  vulnerability (path traversal / arbitrary object read) and the boundary is on us.
- **GPU backends are never publicly reachable.** The gateway reaches them over an
  authenticated, non-public link and each requires its own token; CloudFront never forwards
  to them. The transport — a VPN, a private network, or a provider tunnel — is an
  implementation detail, not a fixed route.
- **Per-key quota and rate limits are enforced server-side**, and keys are revocable.

## Scope discipline

- **Deferred is not rejected.** Follow the razor's stance: delay a component until its
  stated trigger appears, and record the trigger instead of the wish.
- **Two parallel interface paths usually mean one constraint should be deleted.** Prefer
  removing a path over maintaining both.
- **Do not add ASGI layers, caches, state machines, or health-probe loops ahead of need.**
  The stated thresholds decide when they return.

## Environment separation

- **dev and prod use separate buckets and separate CloudFront distributions.** The dev
  bucket never faces participants.
- **Dev output must be byte-comparable with prod output**, or "it worked in dev" proves
  nothing. Pin interpreter and dependencies with `uv.lock`; write images verbatim rather
  than decode-and-re-encode.
- **Prod dataset publishing runs where the data is**, in the same region as S3 (a temporary Tokyo
  EC2 instance), never on the small cloud host.

## Contract sources

- Only two handwritten *contract* sources exist: the Pydantic models in `gateway/` (API) and
  `docs/data-layout.md` (data layout). Reference pages, policy prose, and examples are also
  written by hand, but anything schema-shaped is a projection of those two.
- The generated API markdown is committed. The drift check regenerates it in the gateway
  environment — the only one that can import the app — and runs `git diff --exit-code`.
