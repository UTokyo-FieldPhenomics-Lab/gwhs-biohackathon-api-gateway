# Gateway Rules

Package: `gateway/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **Stack**: FastAPI + uvicorn + Pydantic v2, deployed with systemd. Docker is an escape
  hatch for hosted platforms only; the gateway does not run in a container.
- **Packaged, not run from source.** The gateway is an installable package
  (`src/biohack_gateway`) launched as `uvicorn biohack_gateway.app:app`; no code depends on the
  working directory, so a script in any subdirectory can import it.
- **Every request is key-authenticated.** Enforce a global and a per-key concurrency
  gate; return `503` / `429` with `Retry-After` when exceeded. Endpoints carry no version
  prefix; the surface is frozen for the event instead.
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
- **Every endpoint requires a key.** Images reach clients through the public `data.*` plane, so
  nothing has to be anonymous.
- **Contract source is the Pydantic models.** Invalid input answers `400`, never FastAPI's
  default `422`; error bodies keep one shape.
- **No ML dependencies.** `torch` and friends belong to the model repository; CI splits by
  path so gateway checks stay fast.
- **Stay small.** The model surface stays near 400 lines; the dataset surface adds roughly 200
  lines on top for index load, addressing, list, and facets. Growth past that is a signal to stop
  and reconsider.
