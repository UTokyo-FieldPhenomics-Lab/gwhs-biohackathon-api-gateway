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

- **Bytes never traverse the gateway.** The API answers with the public `data.*` URL of a
  static object; it never serves bytes and never builds a per-request redirect. Inlining
  image bytes pulls bandwidth back onto the gateway and voids the CDN and cost model.
- **Static before dynamic, with one named exception.** If data can be delivered as objects,
  do that. Dataset metadata is that exception: the gateway serves it from memory because a
  republish must take effect immediately and because no client should have to hold a copy.
- **Single source of truth.** The publisher generates the metadata index once from the pinned
  upstream revision; the gateway loads it at startup and never edits, augments, or re-derives
  it. Nothing hand-edited may enter it.
- **S3 is frozen at publish.** An image object's path is written once and never overwritten;
  changed content means a new path. With no version prefix a change is a new path rather than an
  invalidation, and there is no second copy to keep consistent.
- **One dataset, one id space.** An address is the absolute row number of the pinned upstream
  revision, and it is meaningless without its dataset because row numbers repeat across
  datasets. A position inside a filtered result is a cursor, never an identifier.
- **The contract is frozen for the event.** There is no version prefix, so no breaking change
  to a published endpoint is possible — only additive ones.
- **No hashing capability.** Nothing here computes or stores a content hash or checksum.
  Identity comes from names, keys, and platform tokens (`ETag`, S3 native checksums, image
  digests, object versions, upstream revisions). A content hash returns only against a
  measured failure.

## Security

- **No secrets, ever, including git history.** Bucket names, account IDs, and keys arrive
  via SSM or environment variables, never in the repository.
- **Resolve addresses, never concatenate them.** A row is a lookup key in the closed in-memory
  index, so reaching an object key or a filesystem path is impossible by construction. This is
  the most likely real vulnerability and the boundary is on us.
- **A client-supplied URL is parsed, never fetched.** It is decoded into `(dataset, row)` and
  then resolved through the same lookup. Fetching it would turn the gateway into a proxy into
  the private network, GPU backends included.
- **The overlay is not assumed to be ours alone.** A private network can carry other people's
  devices, so anything bound to it is isolated by tag and access policy, with the per-service
  token as the second lock rather than the first.
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

- **One bucket and one distribution per environment, and a dev pair never faces
  participants.** Today exactly one environment exists — the Frankfurt host and its bucket — so
  there is no dev pair to keep apart. When the need for a private place to rehearse appears, it
  becomes its own bucket and its own distribution, never a prefix inside the live one; until then
  the rehearsal target is the dev-only dataset `gwhs-temp` (10 rows) published into the same
  bucket, which exercises the whole path for the price of ten objects.
- **Output is a deterministic function of the pinned revision**, not an artifact of the machine
  that produced it. Pin interpreter and dependencies with `uv.lock`; write images verbatim rather
  than decode-and-re-encode. With one environment this is what makes a republish reproducible
  rather than merely repeated, and it is what a second environment would be compared against.
- **Publishing runs where the cache is, in the bucket's region, and streams.** The cache is
  fetched onto the API host by `biohack-datasets-fetch` — 1.88 GB for all three datasets,
  disposable and rebuilt from the pinned revision — and the bucket sits in that host's region, so
  a publish reads a row's bytes out of the cached parquet and PUTs them in one request. Nothing is
  staged on disk and no scratch volume is involved; moving a publish elsewhere means moving the
  cache with it. Only the metadata index travels the other way. Republishing is a re-run of that
  same deterministic command, which is the only rollback mechanism there is.

## Contract sources

- Only two handwritten *contract* sources exist: the Pydantic models in `gateway/` (API) and
  `docs/data-layout.md` (data layout). Reference pages, policy prose, and examples are also
  written by hand, but anything schema-shaped is a projection of those two.
- The generated API markdown is committed. The drift check regenerates it in the gateway
  environment — the only one that can import the app — and runs `git diff --exit-code`.
