# GPU occupancy, third pass: the container no longer blocks its event loop

The archived baseline is [`2026101923_gpu-occupancy.md`](2026101923_gpu-occupancy.md), whose data
lives in [`2026101923_gpu-occupancy/`](2026101923_gpu-occupancy). This note records the identical
measurement repeated after `models` commit `14919a9`, and what the two can and cannot be compared on.

## What changed

Both containers' `POST /models/{id}/predict/upload` ran decode and inference on the uvicorn event
loop, so the container answered nothing else — `/health` included — for the whole upload. `14919a9`
moves that work to the thread pool (`run_in_threadpool`), where the GPU lock is already taken; the
card still runs one inference at a time. Only the two upload routes changed. Verified in the running
containers on all three machines (`docker exec … grep -c run_in_threadpool /app/serve.py` → 2), and
`nvidia-smi`-visible images were rebuilt at 15:29Z / 15:35Z / 15:58Z on 2026-10-19.

## Method

Identical to the baseline, from the same harness directory and with `load.py` and `rerun.py`
untouched:

- second gateway on `gwhs-host:8001`, `0.0.0.0:8001`, source copied from this checkout
  (`gateway/src/biohack_gateway`, md5 of `app.py` matches), `BIOHACK_MAX_CONCURRENT_PER_TOKEN=0`,
  backend token from `/home/admin/.config/biohack-gateway/env`, log `$HOME/gw-test/gateway.log`;
  one warm request first, and its `inference …` line confirmed in the log before the load began;
- two 60 s runs, 12 workers, one request after another: `run_row_r3` (01:05:55–01:06:56 JST) then
  `run_upload_r3` (01:08:01–01:09:03 JST), all 200;
- `nvidia-smi --query-gpu=utilization.gpu,memory.used,power.draw -lms 250` on `lab-3090` (this
  machine), `gwhs-homepc` (4090) and `lab-5090`, bracketing each window;
- the `/health` sampler on the gateway host as the secondary series, unchanged.

Two deviations, neither of which touches the measurement:

- `analyze_r3.py` is `analyze_r2.py` with `_r2` → `_r3` in the run directory name and the docstring;
  `diff` shows those lines only. `compare_r3.py` (new) does the arithmetic of the comparison.
- The baseline's `images/` directory was no longer on disk, so the four upload payloads were fetched
  again from `data.global-wheat.com`. They are the same immutable objects: the multipart
  `bytes_sent` values in `client.csv` are identical to r2's for all four files
  (178433/405134/326213/664024 plus the 2-byte boundary difference for two-digit worker indices).

## The table

Utilisation is the mean of the in-window 250 ms samples; card-ms per request is the same
mean × window ÷ requests the gateway routed to that machine. r2 is the baseline, r3 this run.

| mode | window | requests | 3090 util | 4090 util | 5090 util | 3090 ms/req | 4090 ms/req | 5090 ms/req | p50 | p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| row r2 | 60.89 s | 891 | 38.8 % | 30.9 % | 18.0 % | 85.9 | 57.0 | 38.2 | 687 | 1388 |
| row r3 | 61.01 s | 923 | 23.8 % | 29.4 % | 24.2 % | 52.7 | 52.9 | 47.8 | 675 | 1207 |
| upload r2 | 62.95 s | 331 | 14.9 % | 12.2 % | 8.9 % | 83.6 | 68.8 | 52.1 | 2135 | 3826 |
| upload r3 | 61.98 s | 348 | 10.6 % | 9.3 % | 8.8 % | 52.2 | 55.8 | 45.9 | 1969 | 3548 |

Aggregates: mean of the three cards' utilisation 29.2 % → 25.8 % (row) and 12.0 % → 9.6 % (upload);
all three cards' busy time ÷ all requests 59.9 → 51.2 card-ms per request (row) and 68.4 → 51.2
(upload). Throughput 14.63 → 15.13 req/s (row, +3.4 %) and 5.26 → 5.61 req/s (upload, +6.7 %).

## Deltas that matter

| | row | upload |
| --- | --- | --- |
| requests completed | 891 → 923 (+3.6 %) | 331 → 348 (+5.1 %) |
| throughput | 14.63 → 15.13 req/s (+3.4 %) | 5.26 → 5.61 req/s (+6.7 %) |
| client p50 | 687 → 675 ms (−1.7 %) | 2135 → 1969 ms (−7.8 %) |
| client p95 | 1388 → 1207 ms (−13.0 %) | 3826 → 3548 ms (−7.3 %) |
| mean utilisation (three cards) | 29.2 → 25.8 % | 12.0 → 9.6 % |
| implied card-ms per request | 59.9 → 51.2 (−14.5 %) | 68.4 → 51.2 (−25.2 %) |

Row mode's code path did not change at all in `14919a9`, so the row column is the run-to-run noise
floor of this harness: +3.6 % requests, −1.7 % p50, −13.0 % p95, and 15 points of utilisation on one
card, with nothing in the change to explain any of it. Upload is where the fix could act, and there
the requests served rose 5.1 % in a window 1.0 s shorter while both p50 and p95 fell by 7-8 %.

## `/health` during uploads, and overlap per machine

`in_flight` is 1 exactly while the container holds the GPU. In the baseline the sampler's upload
window read `0` in 701 of 704 in-run samples (the 5090 answered `1` twice and once, i.e. 3 of 704);
in r3, 46 of 696 samples read `1`, on every one of the six containers:

| backend | r2 in_flight=1 | r3 in_flight=1 | r2 max gap | r3 max gap |
| --- | --- | --- | --- | --- |
| lab-3090:12711 / :12712 | 0 / 0 | 6 / 13 | 777 / 637 ms | 583 / 583 ms |
| homepc-4090:12711 / :12712 | 0 / 0 | 12 / 1 | 632 / 1258 ms | 562 / 563 ms |
| lab-5090:12711 / :12712 | 2 / 1 | 8 / 6 | 739 / 729 ms | 623 / 632 ms |

So yes: `/health` now reports the container busy while an upload is in progress, and it no longer
stalls — the worst sampling gap during uploads fell from 1258 ms to 632 ms. It still answers in the
gaps between requests, which is why only 5-11 % of samples are busy rather than most of them.

The gateway's queue figures show the same overlap as before — the fix did not change dispatch, only
whether the container looks alive while it works. Share of uploads whose `machine_ahead` ≥ 1, and
the maximum: 3090 92 % / 3 (r2) → 93 % / 3 (r3); 4090 96 % / 3 → 92 % / 3; 5090 80 % / 2 → 84 % / 3.
Mean `ahead` (requests already counted against the same container) 0.67 → 0.76, 0.79 → 0.83,
0.55 → 0.48. Two to three requests were queued against a card that can run one at a time, in both
runs.

## What makes the comparison unfair

- **Row mode cannot move.** `14919a9` touches only the upload routes, so every row-mode delta above
  is noise (its own p95 moved 13 %), not the fix. Do not attribute row-mode numbers to it.
- **Model mix per machine is not held constant.** Routing is per request over free lanes, so the
  heavy model (sam3.1 at 1008 px) landed differently: upload on the 3090 was 56 sam / 56 segformer
  in r2 but 32 / 94 in r3, and on the 4090 56 / 56 → 75 / 28. Per-machine utilisation and card-ms
  per request inherit that mix; only the all-cards aggregate is mix-insensitive, and even it is not
  fully.
- **Utilisation is the noisiest series here.** On the 3090 in row mode it fell 38.8 → 23.8 % while
  the gateway-measured, uncontended service time was unchanged (sam 476 → 469 ms, segformer 331 →
  331 ms) and in-window mean power fell only 208 → 194 W. The counter moved far more than the work
  did, which is a warning about single-run utilisation figures in general.
- **The quoted baseline in the hand-over is not one run.** 891 requests / 60.9 s and 331 / 62.9 s are
  `run_*_r2`, but p50 716 / p95 1399 and 2183 / 4148 are `run_*` (the first, GPU-unsampled attempt).
  The r2 latencies are 687 / 1388 and 2135 / 3826, and r1's request counts are 865 and 309 — the
  spread between r1 and r2 is itself as large as any r2→r3 change.
- **Containers were restarted** for `14919a9` (up 41 / 36 / 13 minutes at measurement time; the 5090
  was restarted 12 minutes before its run). Fresh processes mean cold caches for the first seconds;
  hundreds of requests later that is not material, but it is a difference from the baseline's
  long-running containers.
- **The 5090 sampler quirk is unchanged**: the powershell pipeline through ssh exits 1 while still
  writing its lines (267-280 ms intervals, 239-244 lines, 0.99 coverage) — identical in both runs, so
  it does not favour either, but its series has ~2x fewer independent updates than the lines.
- **This machine is both the generator and the 3090 host**, in both runs: 12 client threads and the
  local sampler compete with the two containers' CPU side, so the 3090's numbers and the client
  latencies carry that. No production inference touched any backend in any of the four windows
  (`journalctl -u biohack-gateway` shows zero `inference` lines), and windows were 60.9/61.0 s and
  62.9/62.0 s.

## Where the data is

```
.agents/references/2026101923_gpu-occupancy/
├── run_row_r3/     client.csv gateway.log health.csv gpu_lab-3090.csv gpu_homepc-4090.csv
│                   gpu_lab-5090.csv gpu_launch.json meta.json nvidia-smi.json
├── run_upload_r3/  the same nine files
├── analyze_r3.py       analyze_r2.py with the run directory renamed
├── summary_r3.json     output of analyze_r3.py
├── compare_r3.py       r2 vs r3 arithmetic
└── summary_compare_r3.json
```

`images/` was fetched again for the run and is **not** archived, as in the baseline: the four payloads
are dataset objects and the client CSV records their exact byte counts.
