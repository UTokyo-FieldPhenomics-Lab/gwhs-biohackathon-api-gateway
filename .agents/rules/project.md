# Project Rules

These constraints apply across packages. Change a blocking rule with the implementation and explain
why. Rules contain constraints, short reasons and evidence links; history and measurements belong
in `references/`. How to decide what to build is in [`design-philosophy.md`](design-philosophy.md).

## Architecture and identity

- **Static image reads bypass the gateway.** Return a public `data.*` URL, not image bytes or a
  per-request redirect, to keep bandwidth on the CDN. Participant uploads are the only exception:
  temporary request spooling and forwarding are allowed, but no retained upload or dataset object.
  [Architecture](../../README.md#architecture), [upload bounds](../references/2026102105_gateway-resource-bound-fixes.md).
- **Dataset metadata is the named dynamic exception to static-first.** Load the publisher's pinned
  index once into memory; serve metadata and annotation geometry without editing or re-deriving the
  source artifact. Refresh by service restart, never by an assumed live reload. Images and raster
  masks remain static objects. [Dataset surface](../references/2026101519_dataset-surface.md).
- **Published paths are write-once.** Changed content requires a new path or dataset id, not a cache
  invalidation or publisher overwrite. This avoids mutable copies of one identity.
  [Data layout](../../docs/data-layout.md).
- **An address is `(dataset, absolute row of pinned revision)`.** Row numbers repeat across datasets;
  a filtered position is a cursor, never an id. Preliminary releases use their own dataset id to
  avoid renumbering existing addresses. [Layout](../../docs/data-layout.md).
- **The event contract has no version prefix and only additive changes.** Pydantic models and
  `docs/data-layout.md` are the two handwritten contract sources; schema-shaped references and
  examples project them rather than define a second schema. [Contract](../../docs/data-layout.md).
- **Do not compute or maintain local content hashes or checksums.** Use canonical request text for
  cache keys and upstream revisions, names, object versions or platform-issued tokens for identity.
  Treat ETags as opaque; test doubles do not need hash implementations. Reconsider only against a
  measured failure. [Reasoning](design-philosophy.md),
  [removal](../references/2026102115_documentation-and-implementation-alignment.md).
- **Reserve 1 GB of every workstation GPU for its desktop.** Admit against card capacity minus that
  reserve, including hand-run containers, so inference does not claim the display's last memory.
  [Pool constraints](backend-pool.md).

## Security and admission

- **Keep secrets and real machine coordinates out of tracked files and history.** Credentials,
  bucket/account identifiers and deployment-specific addresses, node names, users and paths belong
  in external host state or ignored `.agents/local/`. Documentation may use standard install-path
  defaults and illustrative address ranges, never real connection details. Raw machine logs are not
  committed. [Commit checks](../../AGENTS.md#commits).
- **Use aliases as labels, not dialable addresses.** `backend-pool.md` is the explicit alias inventory;
  `.agents/local/pool.md` alone resolves machines and routers to private coordinates. Stop with that
  file's path if it is absent; never guess an alias-based URL.
  [Inventory](backend-pool.md), [private-state constraints](deploy.md).
- **Resolve inputs through the closed index, never concatenate paths or fetch participant URLs.**
  Decode an accepted URL to an address before lookup, avoiding traversal and SSRF into backends.
  [Gateway constraints](gateway.md).
- **Backends require a non-public authenticated link and a service credential.** CloudFront never
  forwards to them. Do not rewrite a shared overlay policy unilaterally; record the deviation from
  tag isolation and retain the service token. Do not substitute caller-address allowlists for that
  credential because NAT/proxy paths differ. [Private network](../../deploy/gpu-backends-aws.md).
- **Per-team inference allowance is a fixed policy, default three, not pool width.**
  `BIOHACK_MAX_CONCURRENT_PER_TOKEN` is read at startup; `0` disables it for measurement. Excess work
  is refused immediately with `429`. Uploads take a lane in middleware; rows only after a cache miss;
  metadata and cache hits take none. Token revocation is supported and team ids provide attribution.
  [Implementation and tests](../references/2026102115_documentation-and-implementation-alignment.md).
- **Do not add request-rate or lifetime-count quotas without their triggers.** Rate limiting returns
  for observed bursts not contained by inference admission; a persistent count returns for
  per-request billing or measured inter-team unfairness. Global GPU admission, per-model caps and
  per-card ledgers remain deferred until justified separately; upload resource gates are distinct.
  [Deferred work](../plans/202610171908_fifty-teams-and-more-models.md).

## Environments and publication

- **A second environment gets its own bucket and distribution, not a live-bucket prefix.** Until a
  separate rehearsal environment is needed, use the development dataset in the existing environment.
  Public distribution requires cleared licensing. [Publication](datasets.md).
- **Host state stays outside the checkout.** Keep environment, tokens, indices, model configuration,
  disposable cache and publisher credentials under `~/.config/biohack-gateway/`. Exclude the complete
  `publish/` directory from the service with `InaccessiblePaths=`; file modes alone cannot separate
  two processes running as the same user. [Host isolation](deploy.md).
- **Make published content reproducible from pinned provenance and locked tooling.** Preserve
  upstream image bytes instead of transcoding; machine-local state must not define the release.
  [Determinism](datasets.md).
- **Publish where the upstream cache is, in the bucket's region.** Read parquet serially and upload
  row bytes directly; do not stage image objects or allocate scratch storage. A moved publisher
  needs its cache rebuilt or transferred. Indices may travel separately; rerunning the pinned
  publisher reconstructs them. [Streaming](../references/202610131500_data-plane-region-and-streaming.md).

## Scope and guidance

- **Defer until an observed need, recording its trigger.** Do not prebuild ASGI layers, caches,
  state machines or health loops. Existing authentication, liveness, result caching and backend
  probes have distinct measured responsibilities. [Decision process](design-philosophy.md).
- **Prefer deleting a redundant interface to maintaining two.** Reuse platform and dependency
  primitives before adding another source of truth. [Decision process](design-philosophy.md).
- **The README is the maintainer entry and command reference, one section per subproject.**
  Participant tutorials belong in `docs/content/`; host operations belong in `deploy/README.md`,
  linking back to command descriptions instead of duplicating flags.
  [Documentation alignment](../references/2026102115_documentation-and-implementation-alignment.md).
- **A procedure states the decision the reader executes, not the argument for it.** `deploy/` and
  `docs/` carry the option in use and the detail needed to carry it out. Rejected alternatives,
  measurements, incident history and "why not X" belong in `references/`, linked once. A reader
  weighing two designs is reading a record, not a runbook.
  [The split that prompted this](../references/20261027_aws-gpu-backend.md).
