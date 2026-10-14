# The first live endpoints: health, the error contract, and key authentication

Status: done — implemented and verified on the host on 2026-10-14 (see
[`../references/2026101415_first-live-endpoints.md`](../references/2026101415_first-live-endpoints.md)).
Owner files: `gateway/src/biohack_gateway/app.py`, `gateway/src/biohack_gateway/auth.py` (new),
`gateway/tests/test_app.py`, `gateway/tests/test_auth.py` (both new), `deploy/setup-gateway.sh`.

## Why now

`https://api.global-wheat.com/` answers today, but it answers nothing: `/openapi.json` declares zero
paths, every request lands on FastAPI's default `{"detail": "Not Found"}`, and no request is
authenticated — the deployed surface proves the deploy plane, not the API. Three things make it an
API, and they are one piece of work because they are one file's worth of wiring:

1. **`GET /health`** answers `200` without a key, so the deploy smoke test has something real to
   assert instead of "some status that is not 502".
2. **One error shape on every failure.** The `ErrorResponse` contract exists in the source but no
   request can reach it, so it is an intention rather than a behaviour.
3. **Keys are real.** The pool's write half shipped with the token command; the read half is what
   makes the rule "every endpoint requires a key" true instead of aspirational.

Decided with the user: the health and documentation endpoints carry no key (they expose the contract,
which is already public in this repository, and a browser cannot put a header on the Swagger page);
`/health` alone is enough, no `/status`.

## Shape

- **`auth.py` owns the pool's read half.** `Authenticator(path)` inverts the pool once into
  `token -> team_id` and answers `team_for(token)`. A missing file is an empty pool (nobody has been
  issued a token yet); an unreadable one is a `TokenPoolError` that the caller turns into `503`
  `unavailable`, because a misconfigured pool is not the caller's fault. Success is cached and a
  failure is not, so fixing the file heals the running gateway without a restart.
- **One middleware enforces it, fail-closed.** Everything needs `X-API-Key` except the named open
  paths, so an endpoint added later is protected by construction rather than by remembering a
  decorator. The middleware records the team id on `request.state` and logs one line per
  authenticated request, which is the attribution that stands in for a counted quota.
- **One place builds an error body.** A helper returns `ErrorResponse` for a status and a code, and
  the handlers for validation, `HTTPException`, and unhandled exceptions all go through it. This
  adds `internal_error` to the code set, which the enum allows: the set grows additively.
- **`app.py` gains a factory.** `create_app(tokens_path=...)` is how tests point the app at a
  throwaway pool without monkeypatching; `app = create_app()` stays for
  `uvicorn biohack_gateway.app:app`.
- **The smoke test asks `/health` and requires `200`.** With authentication in place, `/` answers
  `401`, so the old "any status but 502" assertion would pass while telling us nothing.

## Out of scope

- The dataset and model surfaces, the concurrency gates, the per-key rate limit, and the access-log
  format: those need the metadata index and the settings layer.
- Declaring the `X-API-Key` scheme in the OpenAPI document. It becomes worth doing when a real
  protected route exists for the docs page to show it on.

## Verification

```bash
cd gateway && uv run pytest && uv run mypy && uv run ruff check . && uv run ruff format --check .
```

On the host, after the pull:

```bash
curl -s  https://api.global-wheat.com/health                  # 200 {"status":"ok","version":"0.1.0"}
curl -si https://api.global-wheat.com/ | head -1              # 401
curl -s  https://api.global-wheat.com/ | grep invalid_key     # the error body
curl -si -H "X-API-Key: <a token from tokens add>" https://api.global-wheat.com/ | head -1   # 404
sudo bash deploy/setup-gateway.sh                             # smoke passes on /health
```
