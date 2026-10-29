# The campus egress was losing packets, and the pool had no way to say so

Date: 2026-10-29 11:00 JST. Measured from the laboratory 3090 (`lab-3090`) while the campus network
was reported to be in a bad state.

Topic: whether the pool's model of failure can tell a broken link from a broken machine, what the
gateway already absorbs, and the one change made (`_record_failure` and the `/pool` fields).

## What the link was doing

Measured with `ping` and with repeated TCP connects, ten to twenty samples each:

| Target | ICMP | TCP connect |
|---|---|---|
| `lab-3090`, this host | 0% loss | 0.0 ms, mdev 0.0 |
| `lab-5090` | filtered by its own firewall | 1.9–2.6 ms, mdev 0.2 |
| `homepc-4090` | 5/5 then 0/10 within a minute | 22–537 ms, avg 190, mdev 163 |
| the gateway host | 25–35% loss | 250–1290 ms, avg 568, mdev 465 |

ICMP RTT itself was stable where it answered (262 ms to Frankfurt, 257 ms over the tailnet), so the
latency was not the story — the loss was. Counting the kernel's TCP counters around twelve small
requests: 47 retransmitted segments of 2282 to the gateway, 14 of 758 and 38 of 2436 to two public
endpoints used as controls. About 2% of segments, to every off-campus destination, so the loss was on
the campus side and not at one peer.

`traceroute` put the break outside the campus: the room gateway at 0.3 ms, the campus edge at 0.6 ms,
two campus hops at 6.5 ms, the prefectural exchange at 6.6–6.8 ms, then the international transit at
108 ms and 248 ms. Nothing between the campus edge and the transit hop answered at all.

What a participant would have seen, through the live API: `GET /datasets` returned `200` in 2.87 s,
9.07 s and 2.48 s; `GET /health` once timed out at 6 s; the docs site stalled at 30 298 of 31 892
bytes. The data plane did not notice — 278 kB from `data.global-wheat.com` took 0.44–1.08 s.

## Every machine was healthy

`GET /models` reported `backends_up: 3` for `gwfss-segformer-b4` and `2` for `sam3.1-meta`, checked
3–20 s before. Each container's own `/health` answered `200` at the address this host reaches it on.
`homepc-4090` serves `gwfss` only, which is what its host configuration says — its other port
refusing is not a fault.

An upload of 278 kB through the API did complete, three times: 8.4 s, 11.5 s, 14.5 s, at 19–33 kB/s.

## What the design already absorbed

- **The data plane never crossed this link.** Images and masks are `data.*` URLs; CloudFront served
  them at full speed while the API crawled.
- **Row inference is small JSON in both directions** — a request of a few hundred bytes and an answer
  around 26 kB — so 2% loss cost retransmits, not failure.
- **One clock covers the whole request, retries included**, so a fallback cannot spend a second
  budget, and `UNHEALTHY_SECONDS` (30) is asserted `>=` `RESPONSE_TIMEOUT_SECONDS` (10).
- **A send timeout was already not a machine verdict**, which is the one case the budget's author had
  seen before this.
- **Admission follows measured link capacity** (1.5 MB/s seed, 24 MB budget, 20 s wait), so an
  overfull link returned `429` with `Retry-After` instead of queueing.

## The gap this session found

`forward` marked a backend `unhealthy_until = now + 30` on **any** first failure, including a
timeout — and `_ask` set `alive = False` on any non-200 probe. Both are exactly what a dropped packet
produces. On this link that meant the pool would shrink precisely when it was needed, and `/models`
would show a machine missing with no way to tell why.

The failure kinds are not the same fact:

- a **refusal** is a reset from a machine that is up with nothing listening on the port;
- an **oversized** answer is the machine answering something it must not send;
- **unreachable** is a route, a name, or a peer that closed a fresh connection;
- a **timeout** is the only one a lost packet also produces — and the only one that has to happen
  twice (`AMBIGUOUS_FAILURES_BEFORE_PASSING_OVER`).

## What changed

`http_transport` now labels each failed attempt and `Reply.failure` carries it. One function,
`_record_failure`, decides what that means, and both `forward` and the probe read their verdict from
it, so a request and a probe cannot disagree about the same evidence. `/pool` publishes
`last_failure` (`"timeout at connect"`), `failed_seconds_ago`, `failures` and `passed_over` per
machine — the four fields that answer "was it the path or the card?".

`backend.server_errors` also stopped counting network failures: it is documented as a run of `5xx`
*in a row*, and a timeout between two `500`s does not make the application look worse.

Tests that encoded the old rule were updated rather than deleted:
`test_a_connect_timeout_falls_back_to_a_healthy_backend` and
`test_a_fallback_is_given_only_the_budget_the_first_attempt_left` now assert one strike and then two,
and `test_a_burst_of_requests_is_spread_over_both_backends` models a timeout, so it asserts that the
request sent *after* the burst skips the machine instead of asserting there is no such request.

Verified live, from this host, against a closed port and an unroutable test address: the first is
`refused`, the second `timeout` at `connect`, and a live backend is `200` with no kind.

## What was deliberately not changed

The upload ceiling. At 30 kB/s the send allowance (`body / 250 000` + 10 s) runs out somewhere around
300 kB, against a 15 MB edge limit, so a full-size photograph fails with `504 upstream_timeout` on a
link like this. That number is a *budget*, and making it follow the measured link rate would make a
request's duration depend on a shared estimate — the one thing the single clock exists to prevent.
The clients already resize before sending by default, so the documented answer is "send a JPEG around
1000 px", not another mechanism.

## Re-running it

From a machine on the campus network:

```bash
for t in <the gateway's public address> <a public resolver to control for>; do
  ping -c 20 -i 0.2 -W 1 "$t" | tail -2
done
# retransmitted segments over segments sent, around a dozen requests: what a participant feels
awk '/^Tcp:/{print $12, $13}' /proc/net/snmp | tail -1
```

then read `/pool` on the gateway and compare `alive` with `last_failure`: `alive: true` beside
`timeout at connect` is a path problem; `alive: false` beside `refused` is a container that is not
running.
