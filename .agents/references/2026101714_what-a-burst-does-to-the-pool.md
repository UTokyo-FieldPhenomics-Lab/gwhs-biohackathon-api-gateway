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
