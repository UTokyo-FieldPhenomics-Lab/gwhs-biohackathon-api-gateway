# Gateway Rules

Package: `gateway/`. Repo-wide invariants are in [`project.md`](project.md) and are not repeated
here.

- **Stack**: FastAPI + uvicorn + Pydantic v2, deployed with systemd. Docker is an escape hatch for
  hosted platforms only; the gateway does not run in a container.
- **Packaged, not run from source.** The gateway is an installable package (`src/biohack_gateway`)
  launched as `uvicorn biohack_gateway.app:app`; no code depends on the working directory.
- **Every request is key-authenticated, and the gate is closed by default.** One middleware checks
  `X-API-Key` against the token pool before routing, so an endpoint added later is protected by
  existing rather than by remembering a decorator, and a path that does not exist is refused too. The
  exceptions are named in the code — `/health` and the reference page the documentation site frames —
  because they carry no data and could not be opened in a browser if the page itself needed a header.
  An absent pool is a closed gateway rather than an open one, and a pool that has never parsed answers
  `503 unavailable` until it does. Per-key rate and concurrency limits are the intended next layer and
  are **not implemented**; [`project.md`](project.md) holds that decision and its trigger.
- **Images reach clients through the public `data.*` plane**, so nothing has to be anonymous.
- **Routing is a sequential retry over interchangeable backends.** Try backends in order, mark a
  failed one unhealthy for 30 seconds, move on. A backend is any Docker-capable GPU resource behind
  one uniform protocol; the gateway never special-cases a provider. No circuit-breaker, weighted
  routing, or background health loop until there are more than three backends.
- **Two surfaces, one process.** The model surface does auth, gating, and routing; the dataset surface
  serves metadata, facets, and addressing. They share the error contract, authentication, and
  configuration. The dataset surface owns no bytes and calls no backend.
- **The dataset surface is read-only and index-backed.** Metadata is loaded from the publisher's
  SQLite index at startup, and a republish is followed by a restart — that is the whole refresh
  mechanism. Facets are a deterministic projection of that index computed once, never a second store.
- **A dataset's shape is read off the index, not configured.** Which filter keys exist, whether
  geometry is RLE or boxes, whether a mask exists, and which row fields a response carries are facts
  about the published artifact, so a new dataset needs no gateway change and no dataset is
  special-cased in code. A response field is present when the index holds one — one model family with
  `response_model_exclude_unset=True`, never a model per dataset. The surface's two settings — the
  index directory and the base URL images are addressed from — are `create_app` defaults, not host
  environment; moving either is a code change.
- **One geometry encoding across both surfaces.** The model surface returns the same object shape and
  the same COCO RLE for masks as the dataset surface (`{bbox, category?, score?, rle?}`), so a
  participant writes one decoder that serves ground truth and model output alike.
- **Normalisation happens in the backend's image, never in the gateway.** Each model container turns
  that model's native output — SAM 3's raster or compressed raster among them — into that shape
  before it reaches the gateway. The gateway keeps zero ML dependencies and does no encoding.
- **A list carries statistics, never the per-image annotation.** List endpoints return counts and
  presence sets; the annotation itself is one row at a time from the detail endpoint.
- **Addresses are resolved, never concatenated.** A row is a lookup key in the closed index; a
  client-supplied URL is parsed into `(dataset, row)` and never fetched.
- **No bulk endpoints.** The largest page is 1000 rows, and there is no dataset-dump endpoint — a
  client that wants everything reads the index artifact. Batch inference stays deferred until a real
  multi-image loop appears as a need.
- **The reference is usable with a key, and the key stays on the API's origin.** The contract declares
  the scheme the gate enforces and requires it on every operation but `/health`, without which
  Swagger UI carries no credential and every Try it out answers `401`; the page hands the reader's key
  to Swagger UI's own storage, so the browser remembers it between visits. What the field collects is
  typed into the API's page and never reaches the documentation site, which frames the page and does
  nothing else — which is also why only that site may frame it (`frame-ancestors`, configured per
  app), since a field for a secret in a page anything may embed is a field anything may overlay.
- **The reference reads as one document.** Its field wears Swagger UI's own controls and lives in the
  strip the framework keeps for page-wide settings, between the description and the calls, rather than
  in styling of our own. Its groups come out in the order of the paths in the document, which is why
  `/health` is declared before the dataset surface: the one call that needs no key is the one a reader
  can try first.
- **Liveness is a route, not a probe loop.** `/health` answers without a key so a monitor and the
  deploy script can ask it; nothing polls anything else. It reports the installed package version,
  which is how a host running stale code is noticed.
- **The package configures the root logger once, on import.** uvicorn configures loggers of its own
  and never the root one, so without it every line this package writes is discarded before journalctl
  sees it — including the team id each request is attributed to, which is what stands in for a counted
  quota. `basicConfig` leaves an existing root configuration alone, so a deployment's own
  `--log-config` still wins.
- **Contract source is the Pydantic models.** Invalid input answers `400`, never FastAPI's default
  `422`; error bodies keep one shape. Every refusal goes through one helper, the framework's own `404`
  and `405` and an unhandled exception included, and a crash answers `internal_error` with nothing of
  the exception in the body — the traceback reaches the log through the server that re-raises it, so
  the handler does not log it a second time.
- **No ML dependencies.** `torch` and friends belong to the model repository; CI splits by path so
  gateway checks stay fast.
- **The token pool is a file this package owns.** `biohack-gateway tokens list|add|remove --id` is a
  subcommand of the same installed package, so the writer and the reader share one Pydantic model and
  one file format rather than two definitions of it. It edits
  `~/.config/biohack-gateway/tokens.json` — a flat `{team_id: token}` map, tokens verbatim, since
  hashing is a capability this repository does not have — and nothing else: no HTTP surface, no
  `systemctl` call, no absolute host path, so it runs on a laptop against a copy too. `list` prints
  team ids and never tokens, which is what makes it safe to paste anywhere.
- **A pool change applies without a restart, and an index change does not.** The pool's stamp —
  modification time, size, inode — is compared before every request and the file re-read when it
  differs, so issuing, rotating or revoking takes effect on the next request; revocation is the case
  that decides this, because a leaked key has to stop working without a human remembering a command.
  An edit that does not parse never displaces the last pool that did, so a typo cannot lock
  participants out, and one log line names the file. The dataset index keeps the other bargain,
  restart, because a publish is deliberate and large.
- **Stay small.** A second reader of the same index tables, or a second definition of a response body,
  is a signal to stop and reconsider: the surface is two modules on top of a shared index reader, and
  their length is mostly the required NumPy docstrings.
