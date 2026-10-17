# When the pool has to carry fifty teams

Status: **deferred, not started.** This file exists so the next person does not re-derive the shape of
the problem, and so the measurement is run once against a real topology instead of twice against a
guess. Nothing here is scheduled; the trigger below decides when it starts.

## Why it is deferred

The one number a fifty-team test would produce is *where the gateway's queue turns into waiting*, and
that number is a function of the pool's width — `Σ max_in_flight` over the answering backends, which
today is 3 and with the planned models is 9. A curve measured against 3 lanes would be re-measured
anyway once the model and backend counts are fixed, and so would every constant derived from it.

What is already known, measured on 2026-10-17 against three cards
([record](../references/2026101714_what-a-burst-does-to-the-pool.md)): 160 simultaneous calls answered
22.3 rps with no failure and p95 5.99 s. So the load a batched fifty teams would put on the gateway has
already been approximated; what a per-team token run adds is the authentication and counting layers,
which are cheap to check but change no conclusion.

## The trigger

Start when **either** holds:

- the model and backend counts for the event are fixed and `deploy/systemd`/`models.json` reflect them;
- or an operator sees a team's request waiting with no answer for longer than the slowest single call —
  the shape of the unbounded queue below, which is the failure this plan exists to remove.

## What the measurement is

One token per simulated team, minted with `biohack-gateway tokens add`, **not** a raised per-token cap:
the cap is the thing under test, and a load test from one key only measures the cap.

- 50 teams, 3 requests each, released together (a barrier, not a sleep), against the deployed gateway
  over the public hostname — the arrival spread of fifty TLS handshakes is part of what a participant
  experiences.
- Record: status counts, p50/p95/p99, the `computed_by` distribution across backends, and the number of
  requests admitted but not yet dispatched (the gateway's thread-pool queue, which today has no bound).
- Repeat at half the teams and at the estimated peak, so the curve has three points rather than one.
- Then one round of the same shape against a *single* model, to see what the per-model gate does when
  several models share the pool.

## What it decides

Each of these is a separate decision with its own evidence; none is to be implemented before its line
here is answered.

1. **A global admission gate.** Today only the per-token cap exists, so 50 teams may hold 150 requests
   in flight while uvicorn's thread pool runs 40 — and the other 110 wait with no bound and no timeout,
   because `RESPONSE_TIMEOUT_SECONDS` only starts once a thread is taken. If the measurement shows
   admitted-but-not-dispatched time growing with team count, the gate is justified: a bounded queue
   (some multiple of the pool's width) and a wait bound, then `429` with `Retry-After` — the same shape
   the per-token cap already answers.
2. **The per-token cap's value.** It is the pool's width today, which means one team can occupy every
   lane. With fifty teams the question is whether it should be 1 or 2; the measurement decides by looking
   at whether teams that are *under* their own cap still wait.
3. **Per-(team, model) accounting.** One counter per team is right while there is one model. With
   several, a team's three slots are spent across models, so a team at its cap on the model it is not
   using is refused on the one it is. Change the key to `(team, model)` and take the ceiling from that
   model's own width — `ModelRegistry.pool_width` currently returns the widest model, which would be the
   wrong ceiling for every other model.
4. **A per-card memory budget.** A container checks that it fits a card and cannot see what else is on
   it, so two containers on one card may both be admitted against the same 23 GB; the plan's Decision 9
   says a later container crowding out an earlier one is the gate's job, and nothing does that job yet.
   This only matters once a card runs more than one model. Keep it beside `models.json` as host state —
   card, containers, and what each declared — rather than as another service.

## What is not in scope

- Weighted or round-robin routing: idle-first plus shortest queue is measured and balanced, and each
  backend is a serial lane, so there is nothing left to spread.
- A separate queue process, Redis, or a worker fleet: the wait bounded at (1) is the whole of what this
  architecture needs from a queue.
- A result cache: the parameters are per call and a cache would be a second source of truth about an
  answer.
- Anything about the rented `eu-central-1` backend, which is a throughput and availability decision and
  not a concurrency one ([the compute plane](2026101609_gpu-compute-plane.md)).
