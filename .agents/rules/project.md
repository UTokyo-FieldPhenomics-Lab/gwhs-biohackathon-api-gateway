# Project Rules

Binding constraints for this repository. Keep them short; each rule exists because a specific
failure mode was identified. If a rule blocks real work, change the rule in the same PR and explain
why. How to decide *what* to build lives in [`design-philosophy.md`](design-philosophy.md); what a
package may assume lives in [`gateway.md`](gateway.md), [`datasets.md`](datasets.md),
[`docs.md`](docs.md), and [`deploy.md`](deploy.md).

Rules state what must hold and why — never history, measurements, or anecdotes. Those belong in
`.agents/references/`.

## Architecture invariants

- **Bytes never traverse the gateway on the read path.** The API answers with the public `data.*` URL
  of a static object; it never serves bytes and never builds a per-request redirect. Inlining image
  bytes pulls bandwidth back onto the gateway and voids the CDN and cost model. One exception: a
  participant's **upload**, which is a single write from a single client that voids no CDN read. It is
  never persisted and never becomes a dataset object, and the rule beside it does not move — a
  client-supplied URL is parsed into an address, never fetched.
- **Static before dynamic, with one named exception.** If data can be delivered as objects, do that.
  Dataset metadata is the exception: the gateway serves it from memory, because a republish must take
  effect immediately and because no client should have to hold a copy. The exception carries
  annotation **geometry** too, returned on demand; images and masks that are bytes stay in the image
  plane.
- **Single source of truth.** The publisher generates the metadata index once from the pinned
  upstream revision; the gateway loads it at startup and never edits, augments, or re-derives it.
  Nothing hand-edited enters it.
- **S3 is frozen at publish.** A path is written once and never overwritten; changed content means a
  new path. With no version prefix, a change is a new path rather than an invalidation, and there is
  no second copy to keep consistent.
- **One dataset, one id space.** An address is the absolute row number of the pinned upstream
  revision, and it is meaningless without its dataset because row numbers repeat across datasets. A
  position inside a filtered result is a cursor, never an identifier.
- **The contract is frozen for the event.** There is no version prefix, so no breaking change to a
  published endpoint is possible — only additive ones.
- **No hashing capability.** Nothing here computes or stores a content hash or checksum. Identity
  comes from names, keys, and platform tokens (`ETag`, S3 native checksums, image digests, object
  versions, upstream revisions). A content hash returns only against a measured failure.
- **A container never claims a card's last gigabyte.** Admission is computed against the card minus
  1 GB — 23 GB of a 24 GB card, 31 GB of a 32 GB — because these are workstations whose own desktop
  draws on the same card. Stated here rather than left to the deploy script, because a hand-run
  `docker run` bypasses the script while the person sitting at the machine still needs their screen.

## Security

- **No secrets, ever, including git history.** Bucket names, account IDs, and keys arrive via SSM or
  environment variables, never in the repository.
- **Resolve addresses, never concatenate them.** A row is a lookup key in the closed in-memory index,
  so reaching an object key or a filesystem path is impossible by construction. This is the most
  likely real vulnerability, and the boundary is on us.
- **A client-supplied URL is parsed, never fetched.** It is decoded into `(dataset, row)` and then
  resolved through the same lookup. Fetching it would turn the gateway into a proxy into the private
  network, GPU backends included.
- **The overlay is not assumed to be ours alone, and a network we share is not rewritten for us.** A
  private network carries other people's devices, so the target is isolation by tag and access policy
  with the per-service token as the second lock. Where that policy is one file for a network shared
  with people outside the project, the deviation is **recorded rather than taken**: the tailnet keeps
  its everything-reaches-everything policy and the per-service token is the *only* lock, which leaves a
  peer able to reach an inference port and no key material exposed. A lock by address is not the
  alternative it looks like — what a backend sees of its caller is a fact about the **path**, one
  machine's rewriting hop cannot be removed by any router setting — so only a credential carried in the
  request is the same on every path, and only a credential survives a rebuilt host. The record and the
  per-path measurements are in [`../../deploy/README.md`](../../deploy/README.md) §1.3 and
  [`../../deploy/gpu-backends.md`](../../deploy/gpu-backends.md).
- **GPU backends are never publicly reachable.** The gateway reaches them over an authenticated,
  non-public link and each requires its own token; CloudFront never forwards to them. The transport —
  a VPN, a private network, or a provider tunnel — is an implementation detail, not a fixed route.
- **Keys are revocable, and each one is capped at the pool's width.** A token may have as many
  requests in flight as the pool is wide — three against three cards, never four: `ConcurrencyGate`
  counts per team in the request middleware and refuses the next one with `429 quota_exceeded`
  immediately rather than queueing it. The ceiling is the pool's declared `max_in_flight` summed, the
  same number `GET /models` publishes, so a cap that disagreed with the published capacity would be a
  second source of truth about it; a fourth card raises it without a code change. Revocation removes
  the team from the token pool, and attribution comes from the
  team id in the request log. A per-key *request rate* is still absent, and so is a per-key *count*
  quota: a count needs persistence, a lifetime, and a top-up path, and its only effect is a human
  round-trip at the moment a team is blocked. The trigger for a count: a backend billed per request,
  or a fairness complaint between teams. The trigger for a rate: observed bursts that a cap of three
  in flight does not contain. What is deliberately absent *before* a measurement — a global admission
  gate, a per-model cap, a per-card memory ledger — is planned in
  [`../plans/202610171908_fifty-teams-and-more-models.md`](../plans/202610171908_fifty-teams-and-more-models.md),
  together with the trigger that starts it.

## Scope discipline

- **Deferred is not rejected.** Delay a component until its stated trigger appears, and record the
  trigger instead of the wish.
- **Two parallel interface paths usually mean one constraint should be deleted.** Prefer removing a
  path over maintaining both.
- **Do not add ASGI layers, caches, state machines, or health-probe loops ahead of need.** The stated
  thresholds decide when they return. The request gate and `/health` are not that: authentication is
  part of the contract rather than a cache in front of it, and a liveness route something else asks
  is not a loop that polls.

## Environment separation

- **One bucket and one distribution per environment, and a dev pair never faces participants.**
  Exactly one environment exists today, so there is no dev pair to keep apart; when the need for a
  private place to rehearse appears, it becomes its own bucket and its own distribution, never a
  prefix inside the live one. Until then the rehearsal target is the dev-only dataset `gwhs-temp`
  (10 rows) published into the same bucket, and `gwhs` publishes only after its license is cleared
  as `gwhd`'s and `gwfss`'s were. Host state — `env`, `tokens.json`, the index directory, and the
  publisher's `publish/` with its `env` and the console's access-key CSV — lives under
  `~/.config/biohack-gateway/`, outside the checkout; the unit excludes `publish/` with
  `InaccessiblePaths=`, because the service and the publisher run as the same user and file modes
  alone cannot separate them.
- **Output is a deterministic function of the pinned revision**, not an artifact of the machine that
  produced it. Pin interpreter and dependencies with `uv.lock`; write images verbatim rather than
  decode-and-re-encode. With one environment this is what makes a republish reproducible rather than
  merely repeated, and it is what a second environment would be compared against.
- **Publishing runs where the cache is, in the bucket's region, and streams.** The cache is disposable
  and rebuilt from the pinned revision, and the bucket sits in that host's region, so a publish reads
  a row's bytes out of cached parquet and PUTs them in one request: nothing is staged on disk and no
  scratch volume is involved. Moving a publish elsewhere means moving the cache with it. Only the
  metadata index travels the other way. Republishing is a re-run of that same deterministic command,
  which is the only rollback mechanism there is.

## Contract sources

- Only two handwritten *contract* sources exist: the Pydantic models in `gateway/` (API) and
  `docs/data-layout.md` (data layout). Reference pages, policy prose, and examples are also written
  by hand, but anything schema-shaped is a projection of those two.

## Where guidance lives

- **Guidance sits where the work happens, and links rather than repeats.** The commands for running
  something are documented in the README section of the thing they run — the root README, one section
  per subproject — and the operations that touch the host live in
  [`../../deploy/README.md`](../../deploy/README.md). The runbook points at the command reference
  instead of restating its flags, so a reader arriving from either direction gets the same answer and
  every flag is described in one place.
