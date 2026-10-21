# Gateway Rules

Package: `gateway/`. Shared constraints are in [`project.md`](project.md).

## Process, authentication and state

- **Use FastAPI, uvicorn and Pydantic v2 as an installed package.** Launch
  `uvicorn biohack_gateway.app:app` under systemd; no working-directory dependency. Docker is
  reserved for a future hosted-platform requirement. [Deployment](../../deploy/README.md).
- **Authenticate by default in middleware.** Check `X-API-Key` before routing, including unknown
  paths. Only explicitly named `OPEN_PATHS` are anonymous; absent state never opens the gate, and
  an unreadable initial pool answers `503`. [Authentication](../references/2026101415_first-live-endpoints.md).
- **Cap teams at three inferences by policy, independent of pool width.**
  `BIOHACK_MAX_CONCURRENT_PER_TOKEN` overrides the startup default; `0` disables it. Refuse excess
  work immediately with `429`: upload lanes are acquired in middleware, row lanes only after a
  cache miss in the handler. Metadata and cached answers take no lane.
  [Decision and tests](../references/2026102115_documentation-and-implementation-alignment.md).
- **The token CLI owns one flat `{team_id: token}` file.** `tokens list|add|remove` shares the
  reader's model and defaults to `~/.config/biohack-gateway/tokens.json`; `--file` supports copies.
  No HTTP administration, service control, hashing or fixed host path; `list` never prints tokens.
  [CLI](../plans/2026101414_✅token-command-line.md).
- **Reload tokens on file-stamp changes, but indices only on restart.** Compare mtime, size and
  inode before authenticated requests. An invalid edit keeps the last parsed pool and logs once;
  revocation must not depend on restarting. [Hot reload](../references/2026101416_token-pool-hot-reload.md).
- **Liveness reports this process and installed version, without a key.** `/health` does not probe
  dependencies. Configure the root logger once with `basicConfig`, respecting an existing logging
  configuration; request logs attribute work to teams. [Host operation](../../deploy/README.md).
- **Use one error helper and Pydantic contract.** Invalid input is `400`, not `422`; framework
  `404`/`405` and unexpected errors use the same body. An unexpected error exposes no exception
  text and is not logged twice. [Error contract](../references/2026101415_first-live-endpoints.md).

## Routing and resources

- **Route interchangeable backends by expected wait, then retry sequentially.** Score round-trip
  latency plus queued work; reserve depth atomically, summed across models by declared
  `computed_by.machine`. Missing machine names stay separate; same-machine ties prefer a free
  container. No provider special-casing, weighted routing or separate circuit-breaker layer.
  [Burst](../references/2026101714_what-a-burst-does-to-the-pool.md),
  [score](../references/202610192300_lane-width-and-the-score.md).
- **Probe backend health off the request path.** One cancellable task uses
  `PROBE_INTERVAL_SECONDS`; publish `backends_up` and `checked_seconds_ago`. Retry undeclared
  containers on that clock; once read, a declaration requires a restart to change.
  [Freshness](../references/2026101711_pool-freshness.md).
- **Separate request failures from machine failures.** Map backend `400`/`422` to `400` with its
  parameter complaint; other `4xx` become `502` with safe wording, except a rejected backend
  credential becomes `503 unavailable`. Do not retry them. A `500` becomes `502` without retry;
  three consecutive application failures pass the backend over. Connection failures, read timeouts
  and `502`/`503`/`504` allow failover; the exclusion window covers at least the failed attempt's
  response budget. [Failure policy](../references/2026102115_availability-and-failure-policy.md).
- **Distinguish connect, send and read budgets.** A send timeout does not condemn the machine or
  resend its upload. Retries share one elapsed-time deadline; limit response bytes and close
  incomplete exchanges rather than pooling or caching them.
  [Transport bounds](../references/2026102105_gateway-resource-bound-fixes.md).
- **Keep blocking backend handlers synchronous.** A plain `def` runs in the framework worker pool;
  blocking socket calls must not occupy the event loop. Read admitted upload payloads through
  `file.file`. [Evidence](../references/2026101714_what-a-burst-does-to-the-pool.md).
- **Reserve upload receive capacity before multipart parsing.** A full process-wide receiver refuses
  immediately with `429` and `Retry-After`. Temporary spools may consume RAM on tmpfs. Admit parsed
  file size before reading the full payload and release every reservation on every exit.
  [Resource bounds](../references/2026102105_gateway-resource-bound-fixes.md).
- **Queue forwarded uploads by link bytes, not worker count.** Order by `(team_in_flight, arrival)`;
  derive capacity from completed bytes over busy seconds. Defaults seed 1.5 MB/s, cap the budget
  at 24 MB and wait at most 20 s; `BIOHACK_UPLOAD_BYTES` and `BIOHACK_UPLOAD_WAIT_SECONDS` override
  the last two. Refusals include `Retry-After`. Rows bypass this queue; increasing workers does not
  replace admission. [Admission](../references/2026102105_gateway-resource-bound-fixes.md).
- **Cache only successful published-row predictions.** Key by canonical dataset, revision, row,
  model and query text, never a digest. Only a changed revision changes revision-based cache keys;
  a hit preserves original `computed_by`, adds cache provenance and takes no lane. Uploads are not
  cached. The store is disposable; failure is a miss, and old keys need no migration.
  [Cache](../references/2026102115_documentation-and-implementation-alignment.md).

## Data and model contract

- **Use separate address and upload predict routes plus model list/detail routes.** Metadata and
  model surfaces share one process, auth, configuration and error contract; datasets call no GPU.
  [Contract](../../docs/data-layout.md).
- **Serve dataset metadata from the publisher's startup index, without another store.** Derive facets
  once; infer filters, geometry, masks and optional fields from the index, never dataset-specific
  configuration. Use one response family with `response_model_exclude_unset=True`; index directory
  and data base URL are `create_app` defaults, not environment settings.
  [Dataset surface](../references/2026101519_dataset-surface.md).
- **Return public image URLs by lookup; never fetch a participant URL.** Lists expose statistics,
  not per-image annotations. Detail returns one row's geometry; pages stop at 1000 rows. No dump or
  bulk-inference endpoints before a demonstrated need. Index artifacts remain internal, not a
  participant download surface. [Layout](../../docs/data-layout.md).
- **The container owns model parameters.** Publish its declaration and validate names, duplicates,
  types, bounds, choices and declared combinations before forwarding values verbatim. Skip
  declaration-based validation only when `declared` is false; empty declared parameters mean none
  are accepted. Semantics remain the backend's; declared `clamp` ceilings may be forwarded and
  must produce warnings. [Parameters](../references/2026101811_model-parameter-validation.md).
- **Do not expose knobs that can increase the admitted memory peak.** Device and precision are
  deployment properties. Resolution choices require measured values and a declared clamp ceiling,
  not inferred stride ranges. [Parameter design](../plans/2026101811_✅model-parameter-contract.md).
- **Use one object/COCO RLE shape for annotations and predictions.** Backend containers normalize
  native outputs; the gateway has no ML dependency or geometry encoder. Reuse the shared index
  reader and response models instead of adding parallel definitions.
  [Layout](../../docs/data-layout.md), [model rules](models.md).

## Framed pages

- **Keep key entry and live calls on the API origin.** `/docs` and `/play` are anonymous shells
  restricted by `frame-ancestors` to the configured docs origin. The docs site frames them and
  receives no key; no CORS is needed. Swagger's scheme must match the gate, with authorization
  persistence disabled. [Reference](../references/2026101522_interactive-key-in-the-reference.md).
- **Use only `assets/api_key.js` to remember keys.** Both pages share one localStorage entry and
  Forget action; migrate/drop Swagger's legacy entry. If storage is unavailable, retain the key
  only for the page lifetime without throwing. [Shared store](../references/2026101617_one-key-for-both-pages.md).
- **Keep page material beside the package, one language per file.** Inline application assets;
  serve only the three named vendored Swagger files, never an asset-directory mount. Both pages
  reuse the frame reporter and key store; paths must not depend on cwd.
  [Key-page isolation](../references/2026102103_key-page-csp-and-hsts.md).
- **Build playground controls and sample curl from the same model declaration and state.** Only
  presentation defaults may depend on model id; unknown ids use the base view. Preview a resolved
  image before inference. Print `$KEY`, never a token, in the generated command.
  [Playground](../references/2026101622_play-page-layout-annotations-and-curl.md).
- **Reuse the site's client module for decoding and dataset declarations for class colour.** Copy
  row-major masks without another transpose; categories without a dataset palette are coloured per
  object. No parallel decoder or hardcoded class palette.
  [Client rules](clients.md), [colour](../references/2026101918_play-one-colour-for-every-class.md).
- **Fit the whole image in a fixed-height pane.** Use `rem`, not `vh`, in a height-reporting frame;
  draw boxes and labels as image-relative elements over the raster. No scrolling within an image.
  [Layout](../references/2026101622_play-page-layout-annotations-and-curl.md).
- **Make the reference read in contract order.** Put key controls in Swagger's page-settings strip;
  declare `/health` first and make padlocks mean closed before authorization, open after, in words
  and glyphs. [Reference controls](../references/2026101522_interactive-key-in-the-reference.md).
