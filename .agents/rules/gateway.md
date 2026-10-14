# Gateway Rules

Package: `gateway/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **Stack**: FastAPI + uvicorn + Pydantic v2, deployed with systemd. Docker is an escape
  hatch for hosted platforms only; the gateway does not run in a container.
- **Packaged, not run from source.** The gateway is an installable package
  (`src/biohack_gateway`) launched as `uvicorn biohack_gateway.app:app`; no code depends on the
  working directory, so a script in any subdirectory can import it.
- **Every request is key-authenticated.** Enforce a global and a per-key concurrency
  gate and a per-key request rate; return `503` / `429` with `Retry-After` when exceeded. Endpoints
  carry no version prefix; the surface is frozen for the event instead. Counted quotas are absent by
  decision, and [`project.md`](project.md) records the trigger that would bring one back.
- **Routing is a sequential retry over interchangeable backends.** Try backends in order,
  mark a failed one unhealthy for 30 seconds, move on. A backend is any Docker-capable GPU
  resource behind one uniform protocol; the gateway never special-cases a provider. No
  circuit-breaker, weighted routing, or background health loop until there are more than
  three backends.
- **Two surfaces, one process.** The model surface does auth, quota, gating, and routing; the
  dataset surface serves metadata, facets, and addressing. They share the error contract,
  authentication, and configuration. The dataset surface owns no bytes and calls no backend.
- **The dataset surface is read-only and index-backed.** Metadata is loaded from the publisher's
  JSONL at startup, and a republish is followed by a restart — that is the whole refresh
  mechanism. Facets are a deterministic projection of that index computed once, never a second
  store.
- **Addresses are resolved, never concatenated.** A row is a lookup key in the closed index; a
  client-supplied URL is parsed into `(dataset, row)` and never fetched.
- **No bulk endpoints.** The largest page is 1000 rows, and there is no dataset-dump endpoint —
  a client that wants everything reads the index artifact. Batch inference stays deferred until a
  real multi-image loop appears as a need.
- **Every endpoint requires a key, and the gate is closed by default.** One request gate checks the
  `X-API-Key` header against the token pool before routing, so an endpoint added later is protected by
  existing rather than by remembering a decorator, and a path that does not exist is refused too. The
  exceptions are named in the code — `/health` and the generated documentation pages, which carry no
  data and could not be opened in a browser if the page itself needed a header. An absent pool is a
  closed gateway rather than an open one; a pool that does not parse answers `503 unavailable` until it
  does, without a restart; and the pool itself is read once, so issuing a token takes effect at the
  next restart. Images reach clients through the public `data.*` plane, so nothing has to be anonymous.
- **Liveness is a route, not a probe loop.** `/health` answers without a key so a monitor and the
  deploy script can ask it; nothing polls anything else. It is the one place a request is answered
  before authentication, and it reports the installed package version, which is how a host running
  stale code is noticed.
- **The package configures the root logger once, on import.** uvicorn configures loggers of its own and
  never the root one, so without it every line this package writes is discarded before journalctl sees
  it — including the team id each request is attributed to, which is what replaces a counted quota.
  Removing that call silently deletes the attribution, and `basicConfig` leaves an existing root
  configuration alone so a deployment's own `--log-config` still wins.
- **Contract source is the Pydantic models.** Invalid input answers `400`, never FastAPI's
  default `422`; error bodies keep one shape. Every refusal goes through one helper, the router's own
  `404` and `405` and an unhandled exception included, and a crash answers `internal_error` with
  nothing of the exception in the body — the traceback reaches the log through the server that
  re-raises it, so the handler does not log it a second time.
- **No ML dependencies.** `torch` and friends belong to the model repository; CI splits by
  path so gateway checks stay fast.
- **The token pool is a file this package owns.** `biohack-gateway tokens list|add|remove --id` is a
  subcommand of the same installed package, so the writer and the reader share one Pydantic model and
  one lock file rather than two definitions of a format. It edits
  `~/.config/biohack-gateway/tokens.json` — a flat `{team_id: token}` map, tokens verbatim, since
  hashing is a capability this repository does not have — and nothing else: no HTTP surface, no
  `systemctl` call, no absolute host path, so it runs on a laptop against a copy too. `list` prints
  team ids and never tokens, which is what makes it safe to paste anywhere. Issuance is a human action
  on the host, a key-authenticated request is what verifies a token, and the reading half changes
  behaviour on restart like every other read-into-memory surface.
- **Stay small.** The model surface stays near 400 lines; the dataset surface adds roughly 200
  lines on top for index load, addressing, list, and facets. Growth past that is a signal to stop
  and reconsider.
