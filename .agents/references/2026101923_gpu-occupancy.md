# GPU Occupancy While Requests Are Queued

Date: 2026-10-19 23:00 JST, recorded the next morning
Topic: whether a card is ever idle while the pool has requests waiting — the question that decides
if `max_in_flight: 1` is costing throughput. Data beside this note in
[`2026101923_gpu-occupancy/`](2026101923_gpu-occupancy).

## Objective

[The lane-width note](202610192300_lane-width-and-the-score.md) found that the containers serialise
their GPU work behind a lock, and corrected its own first reading. What it could not say is whether
the card has **gaps**: if a card is busy whenever anything is queued, the declared width of one costs
nothing and admission is not worth touching.

## Method

- Same shape as the earlier run: the second gateway on the host, `0.0.0.0:8001`, with
  `BIOHACK_MAX_CONCURRENT_PER_TOKEN=0`, and the same harness in this directory.
- Two 60 s runs of sustained load, 12 workers, each sending one request after another: one run all
  row mode, one run all upload mode.
- Occupancy read **on each machine**, not through the network:
  `nvidia-smi --query-gpu=utilization.gpu,memory.used,power.draw --format=csv,noheader -lms 250`,
  started a second before the load and stopped after it. The driver refreshes those figures about
  every 500 ms, so 250 ms lines come in pairs; the mean is still the driver's average over the period.
- `/health` sampling kept as a secondary series.

**The first attempt was invalid and is kept.** `run_row/` and `run_upload/` sampled each container's
`/health` — which reports whether the GPU lock is held — at 1.8 samples/s over a 0.57 s tailnet round
trip. A lock held for 100–300 ms cannot be seen at that cadence: the 0.14–0.21 "busy" figures there
are an artefact of the sampler, not a measurement of the card. `run_row_r2/` and `run_upload_r2/` are
the run that counts.

`/health` cannot measure uploads at all, even at a good cadence: `/predict/upload` is an `async def`
that runs the inference inline, so the event loop is blocked and `/health` is not answered until the
upload returns. Verified directly — a 189 ms upload left a health call pending for 174 ms and then
answered `in_flight=0`, where a 178 ms row request answered in 17 ms with `in_flight=1`.

## Measured

Row mode, 60.9 s, 891 requests, all `200`:

| machine | mean util | median | > 50 % | requests | implied GPU ms/request |
|---|---|---|---|---|---|
| `lab-3090` | 38.8 % | 28 % | 36.2 % | 275 | 86 |
| `homepc-4090` | 30.9 % | 23 % | 31.1 % | 330 | 57 |
| `lab-5090` | 18.0 % | 6 % | 15.1 % | 286 | 38 |

Upload mode, 62.9 s, 331 requests, all `200`:

| machine | mean util | median | > 50 % | requests | implied GPU ms/request |
|---|---|---|---|---|---|
| `lab-3090` | 14.9 % | 0 % | 10.3 % | 112 | 84 |
| `homepc-4090` | 12.2 % | 0 % | 11.9 % | 112 | 69 |
| `lab-5090` | 8.9 % | 0 % | 6.5 % | 107 | 52 |

Requests were queued throughout: `machine_ahead > 0` on 95 % of row attempts and 92 % of upload
attempts, deepest three.

## Findings

1. **The card was idle most of the time, with requests waiting.** At the driver's own 500 ms
   resolution the row-mode card read 0 % for 31–42 % of samples, with all-zero stretches of 2–4 s; in
   upload mode the zero share was 61–70 % and the longest gaps 4.5–6.5 s. Gaps shorter than half a
   second are below this sampler, and the means above them are unaffected.
2. **The pool is network-bound, not GPU-bound.** One request costs 38–86 ms of card time against
   687 ms end to end in row mode and 2135 ms in upload mode. The 262 ms link, the CloudFront fetch and
   the body's transfer are the rest.
3. **Uploads are serialised by the event loop, not only by the lock.** A container serves one upload
   at a time and can answer nothing else meanwhile, including `/health`. Raising its declared
   `max_in_flight` would promise work it cannot overlap.
4. **A slow upload can make a healthy container look dead.** The liveness probe gives a backend
   `DECLARATION_TIMEOUT_SECONDS` (3 s); uploads in this run reached 4.1 s at p95. A probe landing
   inside one marks the backend down and drops it from the pool for up to a probe interval. It did not
   happen in either run, and it is a risk rather than a defect observed.

## Decided

| Decision | Reason |
|---|---|
| The declaration stays at 1 for now | For uploads a wider declaration buys nothing until the blocking route is fixed; for rows it would raise card utilisation only by overlapping more network wait, and the ceiling then is the gateway host and the containers' thread pool |
| The first attempt is archived, not deleted | Its sampler could not see the event it was measuring, which is the lesson worth keeping |
| The probe budget is left at 3 s for now | Recorded as a risk with the upload figures that make it live; changing it is a decision for the deployment, not a measurement |

## Next

- If admission is to be raised, fix `predict_upload` first: run the inference off the event loop.
- Re-take this measurement after that change, and only then choose a width per machine.
- The 5090 reported a third of the 3090's card time per request for the same model mix; worth
  confirming before any per-machine width is written down.
