# What a burst does to the pool

Measured 2026-10-17 on the deployed gateway (`biohackathon-api-server`, `eu-central-1`) with three
backends serving `sam3.1-meta`: `lab-5090`, `lab-3090`, `homepc-4090`. Two of them were brought up by
[`2026101713_homepc-4090-becomes-a-backend.md`](2026101713_homepc-4090-becomes-a-backend.md); `homepc`
was added as the third entry of `models.json` for the test and has stayed.

The question was whether the pool needs a queue and a load balancer. It needed neither: it needed one
line of routing and one `async` removed.

## How it was measured

`ThreadPoolExecutor` with a barrier, so N requests leave at once; every round recorded each reply's
status, the `computed_by.machine` it named, and its wall time. Address rounds posted
`{"dataset": "gwfss", "row": 4}` to `POST /models/sam3.1-meta/predict`; upload rounds posted a
2048² 1.07 MB JPEG as multipart to `.../predict/upload`. Loopback rounds ran on the gateway host
against `127.0.0.1:8000`, external rounds from the laboratory workstation against
`https://api.global-wheat.com` (263 ms RTT, TLS terminated on the host by Caddy).

## The address route: one card was the whole pool

| Concurrent | Before: p95 | Before: served 5090/4090/3090 | After: p95 | After: served 5090/4090/3090 |
|---|---|---|---|---|
| 12 | 2.04 s | 10 / 1 / 1 | — | — |
| 40 | 4.17 s | 38 / 1 / 1 | **2.86 s** | 14 / 13 / 13 |
| 80 | 6.62 s | 71 / 5 / 4 | **3.61 s** | 27 / 32 / 21 |
| 160 | 11.77 s | 140 / 11 / 9 | **5.99 s** | 60 / 63 / 37 |

Every request answered `200` in every round, before and after. `_order` put idle backends first and
then kept list order, so once all three were busy every further arrival took `busy[0]`: the pool
answered at one card's rate (12.4 rps at 160 concurrent, against 4.8 + 8.7 + 7.8 measured one card at
a time). Sorting the busy group by `in_flight` restored 22.3 rps and a balanced spread.

The 160-round p95 of 11.77 s exceeded `RESPONSE_TIMEOUT_SECONDS` (10 s) with no `failed a` line in the
log. That wait was the gateway's own thread pool (40 anyio threads, `predict` being a `def`), not a
backend socket, so no timeout and no retry ever fired. **The duplicate-inference risk the timeout was
believed to create has not been observed**; it needs one backend's own queue to exceed 10 s.

## The upload route: one upload held the whole gateway

`predict_upload` was `async def` and called the blocking `registry.forward` inside the event loop.
With six concurrent uploads in flight, `/health` answered in **11.08 s and 5.31 s** and returned to
1.2 ms the moment they drained — every route, for every participant, waited behind one upload.

| Round | Before | After |
|---|---|---|
| 6 uploads (loopback), wall | 16.63 s | **3.30 s** |
| 6 uploads, `/health` during | 11.08 s | **1.2 ms** |
| 40 uploads (loopback), wall | 113.55 s | **5.89 s** |
| 40 uploads (external), wall | 106.66 s | **10.79 s** |
| 40 uploads, served 5090/4090/3090 | 40 / 0 / 0 | 14 / 13 / 13 |

**A wrong reading, recorded so it is not repeated:** the first external rounds moved 1.07 MB per
request at ~0.38 MB/s aggregate, and that constant rate across 1, 8, 40 and 80 requests looked like a
3 Mbps uplink. It was the serialization — one transfer at a time over a 263 ms RTT — not a pipe.
With the handler fixed the same 40 uploads move 42.7 MB in 10.79 s (**3.96 MB/s**) over 40 parallel
flows, so the room's uplink was never the limit.

The 80-upload external round before the fix lost 15 requests: they were the test client's own 180 s
timeout, not a server error. No `5xx` was ever returned.

## What this changes in the rules

`gateway.md` said "idle ones first"; the busy group now also sorts by what each backend holds.
The per-key and per-model concurrency gate stays **unbuilt**: 160 concurrent requests completed with
no failure, so the recorded trigger — p95 approaching `RESPONSE_TIMEOUT_SECONDS`, or a fairness
complaint between teams — has not fired. What the gate would add is a bounded, fair wait and an
honest `429` instead of an unbounded one, and that is worth revisiting when the full `gwhs` set is
published and a team can loop over thousands of rows instead of ten.

## Scripts

`/tmp/lt.py` (address, through the gateway), `/tmp/lt_up.py` (multipart upload, `LT_URL` and `LT_IMG`
overridable), `/tmp/ltb.py` (one backend directly, `X-Backend-Token`). They are scratch files and were
not added to the repository; the two properties they found are now tests in
`gateway/tests/test_models_surface.py`.

## The per-token cap, added the same day

The measurements above were taken with one participant key, which is also what a single team's loop
looks like: nothing stopped it from occupying all three lanes. `ConcurrencyGate`
(`gateway/src/biohack_gateway/limits.py`) now counts requests in flight per team in the request
middleware and refuses the fourth with `429 quota_exceeded` instead of parking it. Three is the pool's
declared `max_in_flight`, so the cap and `GET /models` cannot disagree.

Verified against the deployed gateway with six simultaneous calls from one token: **3 × 200, 3 × 429**,
the refusals in 0.08 s, one request per card, and the token's next call answered `200` once the three
drained. The cross-team half — a second key unaffected while the first is at its cap — is covered by
`test_a_token_cannot_hold_more_than_three_requests_at_once`, because the host holds one team's token.

A consequence worth knowing before reading any of the tables above as current behaviour: a load test
from a single token can no longer show throughput, only the cap. Measuring the pool again needs one
token per simulated team.

## What is still unbounded

- **An upload's size.** Nothing limits the body: Caddy's `request_body` is unset, and the handler reads
  the whole file into memory (`file.file.read()`) before forwarding it. The host has **1936 MiB** of
  RAM, so three concurrent 500 MB uploads are enough to have the gateway killed for OOM. The model
  resizes every input to 1008² anyway, so a size cap costs no capability — this is the next thing to
  fix, and it is one line at the edge rather than code in the surface.
- **The wait before a handler starts.** Admitted requests still compete for uvicorn's 40 anyio threads.
  With a cap of three per token, 20 teams can admit 60 at once, and the 41st waits for a thread with no
  bound and no timeout. Only worth instrumenting if the event sees that many teams working at once.
- **The response budget is per socket operation, not per request.** A client can be made to wait
  `CONNECT_TIMEOUT_SECONDS + RESPONSE_TIMEOUT_SECONDS` per backend attempt. No observed failure; the
  160-round p95 of 11.77 s was thread-pool queueing, not this.

## A second pass: the edge, the cap, and the hop the gateway pays for

### An upload over 15 MB is refused at the edge

`deploy/Caddyfile` carries `request_body { max_size 15MB }` and a `handle_errors 413` that answers the
contract's own body, because Caddy's default is an empty `413` a participant cannot tell from a proxy
failure. Verified against the deployed host with a 17 MB JPEG: `413` and
`{"error":{"code":"payload_too_large","message":"the image is over the 15 MB limit; compress it or
convert it to JPEG and send it again"}}`. A 1 MB upload still answers `200`.

15 MB is set by the host, not by taste: it has **1936 MiB** of RAM and holds each upload in memory while
forwarding it, and the ceiling on concurrent uploads is the 40-thread pool — 40 × 15 MB ≈ 600 MB, which
is survivable, while 24 MB would not be. Raising it on site is that line plus `systemctl reload caddy`.

### The cap follows the pool instead of being a constant

`ConcurrencyGate` now takes a callable and is handed `ModelRegistry.pool_width()` — the largest
per-model sum of `max_in_flight` over answering backends, which is the number `GET /models` publishes.
Three cards give three; a fourth card raises it with no code change. `BIOHACK_MAX_CONCURRENT_PER_TOKEN`
overrides it and `0` disables it, both read once at startup.

| What was run | What came back |
|---|---|
| 6 concurrent, no override | 3 × `200`, 3 × `429`, refusals in 0.07 s, one per card |
| override `=10`, 12 concurrent | 10 × `200`, 2 × `429` |
| override `=0`, 24 concurrent | 24 × `200`, 8 / 8 / 8 across the cards, p95 2.33 s |

The override is what a load test uses instead of a second class of credential.

### A kept connection, and the option the override had dropped

`http_transport` now keeps up to four idle connections per backend and retries once on a fresh one when
the peer has closed a kept connection. Measured on the host, one backend at a time (both laboratory
cards are ~260 ms away):

| | Per call |
|---|---|
| a new connection each time | 0.70 s |
| one connection reused | 0.44 s |

Warm single calls **through the gateway** went from **0.76 s to 0.44 s** — more than the handshake,
because pooling exposed a second defect: `_BoundedHTTPConnection.connect` replaced
`HTTPConnection.connect` and dropped the `TCP_NODELAY` that method sets. On loopback a request then cost
81 ms against 41 ms for a socket the standard library had configured, the extra being the delayed
acknowledgement of the write that follows another. On a 260 ms link that wait is bounded by the round
trip rather than by 40 ms. The option is now set, with a test that asserts it, because the failure mode
is a constant invisible tax rather than an error.

## The container keeps its fetch connection too

`serve.py` fetched with `urllib.request.urlopen`, which opens a connection per call, so every
inference paid a DNS lookup and a TLS handshake before the model saw a pixel. `fetch_bytes` now keeps
up to four idle connections per store host, hands each to one request (the fetch runs outside the
inference lock, so a single connection is not shareable), and retries once on a fresh one when the
store has closed a kept connection. `sam31_official/tests/fetch.py` proves all three without weights
or a GPU.

Rebuilt on all three machines from `37bd910` and recreated with the same run line. Warm medians,
measured from the gateway host over one reused connection each, against a 512² tile:

| Machine | First call | Warm median |
|---|---|---|
| `lab-5090` | 0.774 s | 0.408 s |
| `lab-3090` | 0.749 s | 0.458 s |
| `homepc-4090` | 0.641 s | 0.363 s |

**A prediction this corrected.** The gap between one call at a time (0.65–0.96 s) and the marginal
cost under concurrency (0.13–0.21 s) had been read as the container's handshake, so pooling was
expected to save ~0.5 s per call. It saves about **30–50 ms**: warm single calls through the gateway
were 0.44 s before this change and 0.41 s after. Most of that gap was the gateway's own handshake to
the backend plus thread-pool queueing, already removed by the gateway-side pool, and the rest was
measurement noise between differently loaded runs. The rebuild was still worth doing — but it is the
smallest of the three changes, not the largest.

Two operational notes: the 5090's rebuild re-downloaded the 3.5 GB checkpoint because its layer cache
did not survive an interrupted build (the other two rebuilt in seconds from cache), and the Windows
machine needs `Start-Process` to run a build detached over SSH — `start /b` refuses a non-interactive
shell's stdin.
