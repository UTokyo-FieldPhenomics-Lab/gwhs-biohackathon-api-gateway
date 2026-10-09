# Gateway Rules

Package: `gateway/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **Stack**: FastAPI + uvicorn + Pydantic v2, deployed with systemd. Docker is an escape
  hatch for hosted platforms only; the gateway does not run in a container.
- **Packaged, not run from source.** The gateway is an installable package
  (`src/biohack_gateway`) launched as `uvicorn biohack_gateway.app:app`; no code depends on the
  working directory, so a script in any subdirectory can import it.
- **Every `/v1` request is key-authenticated.** Enforce a global and a per-key concurrency
  gate; return `503` / `429` with `Retry-After` when exceeded.
- **Routing is a sequential retry over interchangeable backends.** Try backends in order,
  mark a failed one unhealthy for 30 seconds, move on. A backend is any Docker-capable GPU
  resource behind one uniform protocol; the gateway never special-cases a provider. No
  circuit-breaker, weighted routing, or background health loop until there are more than
  three backends.
- **Contract source is the Pydantic models.** Invalid input answers `400`, never FastAPI's
  default `422`; error bodies keep one shape.
- **No ML dependencies.** `torch` and friends belong to the model repository; CI splits by
  path so gateway checks stay fast.
- **Stay small.** Roughly 400 lines is the target. Growth beyond routing, gating, auth, and
  cost logging is a signal to stop and reconsider.
