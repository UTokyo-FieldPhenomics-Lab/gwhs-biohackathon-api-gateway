# The pool gets a clock: availability is probed, and the count carries its age

Session of 2026-10-17. It started as a question about a number on a page — "is `2 backends up`
hard-coded or live?" — and the answer was *neither*, which turned out to be a bug worth fixing.

## What the number actually was

`backends_up` counted the backends that had **not recently failed**: `not backend.is_passed_over(now)`
against a deadline that only a *failed request* ever set. So:

- a machine that died quietly stayed published as up for as long as nobody called it;
- a machine that was **off before the process started** was published as up too, because it had
  failed against nothing — two switched-off machines made a directory entry reading
  `"backends_up": 2`.

That second one is the lie the user's question was pointing at, and it is the one that matters: the
page told a reader to press Run on a model whose machines were not there, and pressing it cost the
connect timeout to find out.

## What changed

- `Backend` carries `alive` (the last probe's verdict) and `probed_at` (when it was asked). `_ask()`
  is the one place a backend is asked, so the startup declaration and every later probe share it; a
  `200` is the only answer that counts as alive, because a container still loading its weights
  answers `503` and publishing that as up promises a call it would refuse.
- `ModelRegistry.probe()` asks every backend, once per `PROBE_INTERVAL_SECONDS` (15 s), through
  `watch_pool()` — an app task started and cancelled by a lifespan, so a reload never leaves two.
  The transport blocks, so the probe runs in a worker thread; a 3-second wait on a machine that is
  off must not hold the loop that is answering everyone else.
- A failed probe is logged **only when the verdict changes**, so a machine off for an afternoon
  writes two lines, not 480. Measured on the rig: `stopped answering` then `is answering`, nothing
  between.
- `ModelSummary.checked_seconds_ago` publishes the age of the newest look, and the routing decision
  and the published count are now taken from the same predicate (`alive and not passed over`) — so
  `backends_up: 0` means the same thing to a caller as to a reader: a `503`, not a wait.
- The probe asks **whether, never what**. A declaration is a specification; one that changed under a
  running process would be a deployment that has not been restarted, and re-reading it would rebuild
  a page's parameter controls under the reader's hands. A test pins this: a probe answering
  `resolution: 700` leaves `1008` published.

Two rules and two plans said the opposite and are now corrected in place: `gateway.md` had "no
background health loop until there are more than three backends" and "nothing polls anything else",
and the registry's own docstring said the same. The threshold was a bet that an unprobed count would
not matter at one or two machines; the bug above is that bet losing. What did **not** change: nothing
probes a backend on the request path, `/health` still reports only this process and its version, and
no per-host detail (address, GPU, VRAM) is published — the age is the whole of the new disclosure.

## The page, and what it cost

The model row now ends with `· checked N s ago`, that number is grown by a one-second tick from the
number the server reported plus the time since the answer, and the page re-asks **only** the directory
every 10 s while it is visible. A failed ask changes nothing on purpose: the age keeps climbing,
which is what says the page has stopped being told anything. `Run` and that line are set from the same
reading, so they cannot disagree — and a run that finishes while the pool went empty leaves the button
disabled instead of offering a `503`.

Costs, for the record: one `GET /health` per backend per 15 s; the worst case on a dead machine is the
3-second declaration timeout **per pass**, in a worker thread; the page's poll is one authenticated
`/models` per open page per 10 s, which is pure CPU in the gateway. Nothing here touches a GPU.

## Verified by killing a machine

Rig: the stand-in container on `:8098`, the proxy on `:8097`, the gateway probing every 2 s (patched in
the throwaway `serve_gateway.py`) so a check need not wait a quarter minute.

| moment | what the page said |
|---|---|
| connected | `runs at 1008 px · 1 backend up · 1 request at a time · checked 9 s ago` (muted, Run enabled) |
| the container stopped, **no request made** | `runs at 1008 px · no backend is answering, so nothing can be run · checked 5 s ago` — `rgb(179, 38, 30)`, Run disabled |
| the container restarted | `… 1 backend up …`, muted, Run enabled |
| then Run | `1 object · Σ decoded mask = 12,074 px, Σ area = 12,074 px — equal`, the box labelled `0.58` |

Latency to notice: bounded by the poll (10 s) plus one probe interval — about 12 s on the rig with a
2 s probe, so about 25 s in production with 15 s. The API answered `"checked_seconds_ago": 2` at the
2 s interval, which is the same number the page renders.

## What is still open

- **A single failed probe counts as down for up to one interval.** If a busy container ever answers
  `/health` more slowly than the 3 s declaration timeout, a reader sees a red line and a disabled
  button for ≤ 15 s until the next pass — with `max_in_flight 1` and ~1 s inferences that has not
  happened, and the alternative (two strikes) doubles the time to notice a real death. Worth
  revisiting only with a measurement of a probe under load.
- The three older ones stand: the recorded response for the demo panel, the end-to-end rehearsal
  against a real GPU backend, and the second-model invariant check.
