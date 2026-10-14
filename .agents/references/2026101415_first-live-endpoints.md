# The first live endpoints: health, the error contract, and keys

2026-10-14. What turned the deployed surface from "TLS works" into "the API answers". Rules this
produced are in [`../rules/gateway.md`](../rules/gateway.md) and
[`../rules/deploy.md`](../rules/deploy.md); the plan it completes is
[`../plans/2026101415_✅api-auth-and-error-contract.md`](../plans/2026101415_✅api-auth-and-error-contract.md).

## What was actually missing

Before this work, `https://api.global-wheat.com/` answered, but it answered nothing: `/openapi.json`
declared **zero paths**, every request got FastAPI's `{"detail": "Not Found"}`, and anything at all was
served to anybody who asked. The three pieces landed together because they are one file's worth of
wiring: `/health`, one error shape on every refusal, and a key gate.

Order of work: the plan file, then tests, then the implementation — `tests/test_auth.py` and
`tests/test_app.py` were written first and failed on the missing module, which is what kept the shape
of `Authenticator` and `create_app` from being invented twice.

## Decisions taken while implementing

- **Middleware, not a dependency per route.** The gate is applied to the request, so an endpoint added
  later is protected by existing rather than by remembering `Depends(...)`, and a path that does not
  exist is refused too (no unauthenticated enumeration). The cost, recorded in the rules: the OpenAPI
  document does not yet declare the `X-API-Key` scheme, so the docs page has no "Authorize" button —
  worth adding when a real protected route exists for it to appear on.
- **`/health` and the documentation pages are the named exceptions**, at the user's decision. Both
  carry no data, and a browser cannot put a header on the page that lists the endpoints.
- **A missing pool is a closed gateway; an unreadable pool is `503`.** The first is a legal state (no
  token issued yet), the second is the operator's problem, and the caller is told only that the
  gateway cannot authenticate anyone. The log names the file and the parse error.
- **Success is cached, failure is not.** A repaired pool starts working without a restart, which is
  the opposite of what "read once" would give; a *successful* pool is remembered, so issuing a token
  still takes effect at the next restart like every other read-into-memory surface.
- **`internal_error` was added to the frozen code set**, which meant editing
  `tests/test_error_contract.py` deliberately. That is the mechanism working as designed: the set
  grows additively and a growth has to be recorded.

## The finding that only a real server produced

`pytest` cannot see this one: **uvicorn configures loggers of its own and never the root one**, so
every line the package wrote — the pool it read, the `team=…` attribution — was discarded before
reaching journalctl. The attribution is what replaces a counted quota in this design, so it was
invisible in the place it exists for. The tests passed throughout, because `caplog` installs a handler
of its own.

Found by running the real thing locally: a probe module under `/tmp/opencode` serving
`create_app(tokens_path=…)` on port 8123, so the venv's `uvicorn` imports it the way systemd does.
`grep -i "token pool\|team="` on its log came back empty, which is what pointed at the missing root
configuration. The fix is two lines in `app.py` (`logging.basicConfig`, which leaves an existing root
configuration alone so a deployment's own `--log-config` still wins), and the rule records why
removing them would silently delete the attribution. The same probe confirmed the whole surface again
under a real ASGI server: `/health` 200, `/` 401, `/` with a key 404, `POST /health` 405.

## The verification harness lied once

The first host check reported `502` on every request after a restart, which looked like an application
failure. It was mine: `curl -s -o /dev/null` exits `0` even for a `502`, so the loop meant to wait for
the application was a no-op, two `systemctl restart` calls ran inside the same second, and every
request arrived while uvicorn was still binding. The journal showed the application healthy the whole
time. Lesson for the next script: **wait on the status code, not on curl's exit status** — the fixed
version polls `%{http_code}` until `200` with a deadline. The deploy script itself already asserts the
status, and the smoke test it prints came back `200` on the same host.

## The host run

```bash
scp app.py auth.py → /opt/gwhs-biohackathon-api-gateway/gateway/src/biohack_gateway/
scp setup-gateway.sh → …/deploy/            # both files were untracked/modified, so a pull could not deliver them
sudo bash deploy/setup-gateway.sh           # exit 0
#   waiting for the certificate and the gateway to come up…
#   https://api.global-wheat.com/health answered 200 with a certificate the client accepted
```

| Check | Result |
|---|---|
| `/health` | `200 {"status":"ok","version":"0.1.0"}` |
| `/` | `401 {"error":{"code":"invalid_key","message":"send a participant token in the X-API-Key header"}}` |
| `/v1/nothing`, `/anything` | `401` — refused before routing |
| `/docs`, `/redoc`, `/openapi.json` | `200`; the schema declares `/health` and `HealthResponse` |
| `POST /health` | `405 {"error":{"code":"invalid_request","message":"Method Not Allowed"}}` |
| signed with a token from `biohack-gateway tokens add` | `404 {"error":{"code":"not_found",…}}` — the key was accepted |
| wrong key | `401`, and the presented value is not echoed back |
| journal, after a restart with a pool | `INFO biohack_gateway.auth: token pool: 1 team(s) read from …` and `INFO biohack_gateway.app: GET / team=gw-01` |
| pool replaced with `{]. broken`, service restarted | `503 {"error":{"code":"unavailable",…}}`, `ERROR … token pool unusable: …/tokens.json: Expecting property name…`, and `/health` still `200` |
| pool file removed, **no restart** | `401` — the failure was not cached, so it healed |
| from the public internet (check-host.net) | `/health` 200 (Brazil/Iran/Netherlands), `/docs` 200 (Austria/Moldova/Singapore), `/` **401** (Canada/Iran/Netherlands) |

The temporary token was removed afterwards, so the host's pool is absent again — `tokens.json` appears
when the operator first issues one. Nothing else was left behind: the state directory holds only `env`.

The host then moved onto the committed revision rather than the copies carried over for the test: the
three files were dropped (`rm auth.py`, `git checkout -- app.py setup-gateway.sh`), `git pull --ff-only`
brought them back at `568deb4`, all three checksums matched the working tree, and the pulled script was
run again as the update path — exit 0, the smoke on `/health` `200`, `/` `401`, and a clean checkout.
That is the state the next session starts from.

## Left for later

- Declaring the `X-API-Key` scheme in the OpenAPI document, so the docs page can send a key.
- The dataset and model surfaces, the concurrency gates, the per-key rate limit, and the access-log
  format. The gate, the pool, and the error shape are the floor those stand on.
- A host reboot has still not been tested (the units are `enabled`; boot-time start is wired but
  unproven).
