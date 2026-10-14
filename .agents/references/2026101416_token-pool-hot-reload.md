# The token pool reloads on change instead of on restart

2026-10-14. Asked on the host, standing in front of `tokens add` and its "restart the gateway to apply"
line: must it restart, or can it load dynamically? It can. Rules this produced are in
[`../rules/gateway.md`](../rules/gateway.md); it reverses the refresh bargain recorded in
[`2026101415_first-live-endpoints.md`](2026101415_first-live-endpoints.md) for the pool only.

## What was actually happening

Not an architectural necessity but a choice: `Authenticator` read the file once and remembered it. The
part that made it look erratic is that the middleware calls `team_for(request.headers.get("X-API-Key",
""))` — **an empty string still triggers the load**. So any request to a non-open path, including a
scanner's `GET /`, locked the pool in for the life of the process. On the host that is seconds after
startup; a dev machine that has only asked `/health` would have picked up a new token without a restart
and made the rule look unreliable.

The case that decided the change is not addition but **revocation**: a leaked key has to stop working
without a human remembering a second command, and a restart drops in-flight requests and every
in-process counter that the concurrency gates will eventually keep.

## The mechanism

- Before every request the file's stamp — `st_mtime_ns`, `st_size`, `st_ino` — is compared with the one
  taken last time. Equal means no read; different means re-read. One `stat` is the whole cost.
- The inode earns its place: the pool is written by staging a temporary file and moving it into place,
  so every write lands on a new inode even when time and size would coincide. (`tmpfs` timestamps move
  in clock ticks, not nanoseconds, so time alone is not enough.)
- **A stamp is taken whether or not the read succeeded.** That is what keeps a broken file to one log
  line instead of one per request, while still noticing the edit that repairs it.
- **An edit that does not parse never displaces a pool that did.** The last good pool keeps answering
  and `ERROR … token pool unusable: <file>: <parse error>` is logged once per change. `503 unavailable`
  is now only for a gateway that has never had a good pool. The alternative — a typo in a security file
  locking every participant out mid-event — is worse than the misconfiguration it reports.
- The middleware no longer logs that error itself, since the pool now reports it where it is read; the
  middleware keeps answering the shaped `503`.

## Verified against a real server, not only in tests

`pytest` covers the states, but the last round showed that a real ASGI server can refuse to be the same
thing as `TestClient`. A probe module served `create_app(tokens_path=/tmp/opencode/tokens.json)` on
port 8123 through the venv's own `uvicorn`, and the whole story was walked with `curl`:

| Step | Result |
|---|---|
| started with no pool | `/health` `200`, `/` `401` |
| `tokens add --id dev --file …` **while it served** | signed `/` → `404` — accepted, no restart |
| pool overwritten with `{]. broken` | signed `/` → `404`, the last good pool still answering; a second request added **no** second log line |
| pool deleted and recreated with two teams | both new tokens `404`, the pre-repair token `401` — rotated inline |
| pool deleted | signed `/` → `401`, fail closed |

The log trail reads `token pool: 0 team(s) read`, then `1`, then the one `ERROR`, then `2`, then `0` —
each reload visible, none repeated.

## Where the divergence from the index is recorded

The dataset index refreshes by restart, and that stays. The two are not unified because they are not the
same kind of artifact: a pool is edited mid-event and must take effect at once, while a publish is a
deliberate act on a large file, and the rule now says so — so that a later reader does not "fix" the
index the same way, or the pool back.

## Touched

- `gateway/src/biohack_gateway/auth.py` — the stamp, the reload, keep-last-good.
- `gateway/src/biohack_gateway/app.py` — the middleware stops logging what the pool now logs.
- `gateway/src/biohack_gateway/cli.py` — "restart the gateway to apply" becomes "the gateway applies
  this on its next request", with `tests/test_tokens_cli.py` following it.
- `gateway/tests/test_auth.py` — the test that pinned the old behaviour
  (`test_the_pool_is_read_once`) is replaced by five that pin the new one, including a broken edit and
  the count of log lines.
- `.agents/rules/gateway.md`, `deploy/README.md`, root `README.md` — the change is stated where each
  reader meets it, and the runbook loses its `systemctl restart` line from the token block.

Not pushed, by request: the host still runs the previous build until the commits are pushed and pulled.
