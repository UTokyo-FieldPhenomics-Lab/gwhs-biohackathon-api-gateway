# Lane Width, and the Score It Changed

Date: 2026-10-19 23:00 JST
Topic: re-measuring the pool end to end through the gateway, because the 2026-10-16 figure came from
one model and one mode, and the routing change the new numbers forced.

## Objective

The routing rule ordered backends by "idle first, then fewest in flight", which prices one request
already in flight as a whole service time. That was derived from a measurement of a single model
(`sam3.1-official`) on a single path. Two things had changed since: there are two models now, and the
predict surface has two modes — a published `(dataset, row)` that the backend fetches from `data.*`
itself, and an upload the gateway forwards. The questions were: is a backend still one serial lane,
do the two modes cost the same, and where does the pool actually saturate.

## Method

- A second gateway on the host, `0.0.0.0:8001`, running the same code with
  `BIOHACK_MAX_CONCURRENT_PER_TOKEN=0` so one key could carry the load. The production unit was not
  touched, and the instance was stopped afterwards. `$HOME/gw-test/` holds it and its log.
- Levels of 3, 6, 12, 25 and 50 requests released simultaneously, one per simulated team, each on its
  own connection, 60 s of drain between levels.
- Per request: model 50/50 between `sam3.1-meta` and `gwfss-segformer-b4`, mode 50/50, seeded
  shuffle (`20261019`) sliced per level so no two levels sent the same sequence.
- Everything went through the gateway, so the numbers include the token gate, the thread pool and
  the upload proxying. Per-attempt figures came from the new `inference …` log line, which records
  the queue depth **at dispatch** — the field this whole question turns on.
- The generator ran on `labpc-host` (`lab-3090`), which is also one of the three backends.
  That is a confound, and the one methodological flaw of this run: for a third of the requests the
  client and the backend shared a machine.

## Measured

Uncontended (`ahead=0`), gateway-side, so the 262 ms link to Tokyo is included:

| model | row mode | upload mode | body carried |
|---|---|---|---|
| `sam3.1-meta` | 957 ms | 1948 ms | 77 B / 269–664 KB |
| `gwfss-segformer-b4` | 946 ms | 2110 ms | 77 B / 178–664 KB |

**Uploads cost a full extra second, not a tenth of one.** That is the body crossing the ocean with
the request plus the gateway's multipart handling on two vCPUs.

At level 50, where all three carried the same 50/50 mix — `5090` 1.48 s, `3090` 1.42 s, `4090 at
home` 1.49 s (medians). The three cards are indistinguishable at this load, which is why no
per-backend service calibration was added. Over the whole run the raw medians differ (1.50 / 1.42 /
1.65 s) only because each level sends a different mix, and the home machine drew more of the deepest
one — not because it is slower.

Depth did not cost what a serial lane would predict:

| `ahead` | 0 | 1 | 2 | 3 | 5 |
|---|---|---|---|---|---|
| all models and modes, mean ms | ~1.0–2.1 s | ~1.1–1.9 s | ~1.0–4.0 s | ~0.9–2.3 s | ~1.5–2.5 s |

At level 50 the deepest queue on any backend was five, and the median response stayed at 1.4–1.5 s;
the whole burst finished in 11.5 s with **96 of 96 attempts answered 200** — no `429`, no `503`, no
`504`, and no backend marked unhealthy at any level. Throughput was 4.35 requests/s over three
machines, i.e. **~1.45/s per machine, or 0.69 s of work per request** — the same 0.693 figure the
2026-10-16 run found, which means the container accepts about four requests at once and the card
interleaves them rather than serving them one at a time.

At 50 simultaneous, the gateway was the limit, not the GPUs: client-side p95 8.7 s against a
gateway-side maximum of 6.3 s. The difference is the client's own 262 ms leg, TLS, and the 40-thread
limit of the two-vCPU host.

## What was wrong before

The prediction that 50 simultaneous requests would blow the pool up — 17 deep per lane, ~25 s waits,
mass `504` — **was wrong**, and it was wrong for a measurable reason: a lane is four wide, not one,
so the queue is four times shallower than the serial model says. `max_in_flight: 1`, which each
container declares and which `sum()` turns into the per-token cap of three, understates the hardware
about fourfold. Raising it is a fairness decision and was not taken here; the cap is unchanged.

## Decided

| Decision | Reason |
|---|---|
| The score is `rtt + in_flight × 0.25` for rows and `× 0.5` for uploads | One queued request costs a quarter of a service time (0.95 s row, 2.0 s upload, four at once), and the 0.26 s link is the same order — neither term may decide alone |
| The probe is the round-trip measurement | It is the only request to a backend that carries no work; only a `200` counts, and the first sample is assigned rather than blended with the unmeasured zero |
| No per-backend service figure | 1.42 / 1.48 / 1.49 s medians: there is nothing to tell apart |
| No hedge (send twice, keep the first) | A container answers four at once, so a duplicate spends a quarter of a lane for a race it usually wins by 0.26 s |
| No age-aware remaining time | A request that has run past its service time is either nearly done or hung; subtracting its age makes the hung one look free, and that is the case worth avoiding |
| No `tier` field in `models.json` | The measured round trip *is* the class, and it can change during an event; a configured class cannot |
| No continuous ranking of the Tokyo machines | They measured 257 / 262 / 267 ms — within noise. The only real latency boundary is same-region versus across the ocean |

Both terms in the score are now the same order of magnitude, so a backend with one request in flight
is roughly tied with an idle machine across the ocean, and only a queue of two clearly sends traffic
away. Ordering by "idle first" sent it away at one.

## What changed in the code

`DELAY_PER_QUEUED_REQUEST`, `Backend.rtt_seconds`, `_ask` timing, and `_order` as a scalar sort were
committed as `feat(gateway): order backends by expected wait`; the per-attempt log line that made all
of this measurable is `feat(gateway): log one line per inference attempt`. `limits.py` no longer calls
a lane "serial" (it is the *declared* width that the cap follows), and `rules/gateway.md` now states
the score instead of "idle ones first".

## Reproducing it

The harness, the five levels' client records and the gateway's own log lines per level are archived
beside this note in [`202610192300_load-test/`](202610192300_load-test): `run.py` drives the load,
`analyze.py` reads what the two sides recorded, `run.out` is what the run printed. `gateway-full.log`
is the test instance's whole journal, unsliced — startup, declarations, the smoke calls and every
level — so the slices can be checked against what they were cut from. The code that wrote it is commit
`f8f414f`: the copy at `$HOME/gw-test/gateway/src` hashes identically to that commit's
`models.py`, and the source itself is not archived a second time.

1. Start a second gateway on the host from the checkout's code, with the per-key cap off and its log
   where `run.py` looks for it (`$HOME/gw-test/gateway.log` on `gwhs-host`): copy
   `gateway/src/biohack_gateway` to `$HOME/gw-test/gateway/src/`, then run uvicorn with
   `BIOHACK_MAX_CONCURRENT_PER_TOKEN=0` and the backend token from `~/.config/biohack-gateway/env`,
   bound to `0.0.0.0:8001`. The security group does not open that port; the tailnet is the only way in.
2. Put four images in `images/` beside `run.py` — `gwfss_0007.png`, `gwhd_0003.jpg`,
   `gwhs-temp_0004.png`, `gwhs-temp_0009.jpg`, 178–664 KB — taken from the `data.*` URLs of the rows
   they are named after. They are dataset content and are not archived here.
3. `GW_KEY=<a participant token> python3 run.py --levels 3,6,12,25,50 --gap 60 --warm 60`. The key
   comes from the environment and never from the file.
4. `python3 analyze.py` prints the summary. `run.py` needs `ssh gwhs-host` to slice the log by line
   number, which is what makes each level's server-side record exact rather than approximately timed.

## Next time

- Run the generator somewhere that is not also a backend; a third of the requests were client and
  server on the same machine.
- Three to seven samples per `ahead` bucket is thin. A longer level 50 — a minute rather than a burst
  — would fit the queue cost properly instead of by memory of a mean.
- A 50/50 mix of uploads is heavier than the event will be, and uploads are the expensive mode, so
  the latency figures here are pessimistic for a real demo.
- `sam3.1-meta` answered in 195 bytes on every backend, against 7–26 KB for segformer: for that model
  the ocean is paid on the request and the first byte only, and the scheduler's upload premium does
  not apply to it.
