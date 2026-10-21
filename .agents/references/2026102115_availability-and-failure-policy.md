# The pool survives a slow link, one bad request and fourteen teams uploading

> Correction from the subsequent [re-audit](2026102104_gateway-performance-reaudit.md):
> the upload handler reads the entire spooled file into a heap `bytes` object before admission,
> and the development host's `/tmp` is tmpfs. The memory conclusion and the recommendation to
> raise the worker limit below were incorrect. The cache-counter change also introduced a
> connection-initialization lock re-entry, and connect timeouts were misclassified as sends.
> This record is historical evidence, not approval to deploy that revision.

2026-10-21. A second, wider audit (three parallel passes over the gateway, the model proxy, the
dataset surface, the publisher, the clients, deploy and CI) found one bug already on the request
path and a failure policy that turned a single bad request into a model-wide outage. Both were
confirmed here before anything changed. Four items in that report were already closed by the two
commits before it (`2026102103_…`, `2026102112_…`) and are not repeated; the rest are listed under
"What was not done".

## The one that was happening already: a pooled connection's timeout

`http_transport` set `connection.timeout = timeout` and reused connections from a pool keyed by
`(scheme, host, port)` — not by budget. `HTTPConnection` reads its timeout when it **creates** a
socket, so a kept connection kept the budget it was made with. The probe makes its own with
`DECLARATION_TIMEOUT_SECONDS` (3 s) and checks it in; the pool is LIFO; so the next predict usually
took the probe's connection and inherited 3 s. Any inference slower than that was a `504` and the
machine was passed over for 30 s.

Reproduced directly: a keep-alive stub, a connection made with a 0.05 s socket, `_checkin`, then
`http_transport` with a 5 s budget against a handler that sleeps 0.35 s.

| What the call did | Result |
| --- | --- |
| assign `connection.timeout = 5.0` only | `TIMEOUT after 0.20 s` (its socket was made at 0.2 s) |
| also `connection.sock.settimeout(5.0)` | `200` after 1.04 s |

The fix sets the socket's timeout on every call, for the phase it is in.

## The failure policy: what counts as a machine that is gone

`forward` passed a backend over on **any** `5xx` and retried the same body on the next machine. One
request that reliably broke a backend therefore took the model off the pool 30 seconds at a time,
and the retry spread the crash. Three rules now:

- **`500` is the request's, not the machine's.** It is answered `502` once, not retried elsewhere and
  not passed over. Three in a row do pass the machine over, because a backend whose application is
  broken still answers `/health` with `200` — the probe cannot see this one.
- **`502`/`503`/`504` and no connection at all are the machine's**, and are passed over for the
  response budget as before.
- **A timeout while the body was still going out is the link's.** The send and the answer are
  bounded separately (`MIN_SEND_BYTES_PER_SECOND`, 15 MB = 60 s), `Reply.stage` says which half
  ended, and a send-phase timeout leaves the machine in the pool and does not re-send the body.

A slow reader really does end `sendall` on the clock — reproduced with a 1 MB body, a reader at
256 KB/s and a 0.75 s socket: `TIMED OUT after 0.75s`. So the send budget is not a theoretical
concern; at the old 10 s, a 15 MB upload needed a steady 1.5 MB/s to the backend, and the failure
was booked against the machine.

Backend `4xx` is now **mapped**, not forwarded: `400`/`422` become `400` carrying the backend's
sentence (the parameter complaint the caller needs), everything else becomes `502` with a sentence of
this contract's — which also stops an internal address in a backend's body from reaching a
participant.

## The thread pool was sized for a different service

An upload waits for the link **inside its handler**, and a `def` handler holds one AnyIO worker. The
default pool is 40; the designed burst is 50 teams × 3 lanes. Fourteen teams pressing Run together
parked every worker, and a dataset listing queued behind a photograph. `THREAD_POOL_TOKENS` (200) is
set in the lifespan, with the arithmetic in its docstring. The gate on work stays the byte budget and
the per-key lane; this only decides how much *waiting* the process holds.

## Smaller, and each one line of reasoning

- **`/pool` no longer scans the cache.** `ResultCache` keeps `entries`/`bytes` as they are written,
  dropped and pruned, seeded once at open. `SUM(bytes)` over a 2 GB store was reachable by any key.
- **`describe` asks no backend.** The declaration retry moved into `probe()`, which already owns that
  clock; a page load for a model with one dead backend used to wait three seconds per retry window.
- **Bytes that never crossed are not counted as crossed.** `predict_upload` booked the body as
  delivered even when the transport refused or timed out, which raised the measured link rate exactly
  when the path was worst.
- **A gateway with no backend token leaves no lane counted.** The token check now precedes the
  reservation.

## What was measured

- `uv run pytest` — **262 passed**; mypy, `ruff check`, `ruff format --check` clean.
- `uv run --project docs zensical build --strict` — no issues.
- New tests: a reused connection takes the caller's budget (0.05 s socket, 0.35 s answer, `5.0`
  budget → `200`); a send the clock ended is tagged `send`; one `500` answers `502` without touching
  the second machine; three in a row do; a send timeout answers `504`, is not retried and leaves
  `unhealthy_until` at zero; `422`→`400` with the backend's words, `404`→`502` without its address;
  an empty token leaves `in_flight` at zero; describing asks nothing; the cache's counts follow
  writes, replacements and drops, and survive a reopen.

## What was not done, and why

- **Admission was not rewritten on asyncio.** The audit's diagnosis (a parked thread per waiting
  upload) is right; the remedy it suggested is larger than the event justifies, and the thread pool's
  size was the actual defect — the queue's byte budget is still what decides how many uploads are in
  flight. If the waiting ever needs to be free of threads, that refactor stands on its own.
- **Uploads are still read before the byte budget admits them** — and the audits' memory figure is
  wrong: Starlette spools a part over 1 MB to a temporary file, so a 15 MB upload is on disk, not in
  RAM. What a coordinated burst risks is `/tmp` (150 × 15 MB), not the heap. Left recorded rather
  than fixed: the bound is the lanes, and requiring the whole event to collude.
- **The publisher's two footguns, CI's floating actions and `pages: write` on PR builds, the
  clients accepting an `http://` base URL, the missing `X-Content-Type-Options`, and the in-flight
  count keyed by a `machine_key` that could change** are recorded and untouched. None of them is on
  this event's path to an outage.
