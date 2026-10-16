# The tailnet as it is, and whether the gateway host can reach a lab GPU machine

Date: 2026-10-15 14:25 JST.
Topic: what `tailscale status` and `tailscale ping` report on the host, read-only — whether the
campus NAT blocks the gateway from reaching a lab machine, which machine could serve as a backend
first, and what is owed before one joins. It is the inventory the compute-plane decision needs, and it
corrects an open item from
[`202610122311_ec2-gateway-host-bringup.md`](202610122311_ec2-gateway-host-bringup.md) that still reads
as "onboard `lab-pc`, `home-pc`".

**Corrected 2026-10-16.** The paragraph below on what is missing reads as though a candidate backend
machine had to become a tailnet node. It does not: each node named after a room is a **GL.iNet router
advertising its own `/24`**, so the machines behind it are reachable by plain private address with no
client installed at all. Both candidate backends answered the host on 2026-10-16 — 242–250 ms from
home and 337–350 ms from the laboratory
([`2026101610_backend-reachability-over-the-room-routers.md`](2026101610_backend-reachability-over-the-room-routers.md)).

## The question

Can `biohackathon-api-server` (the EC2 gateway host) connect *out* to a lab machine, so inference can
run on that machine's GPU — or does the campus NAT make the direction impossible?

## Direction is not the obstacle; onboarding is

**The host already reaches campus-owned tailnet peers directly.** `tailscale ping` from the host:

| Peer | Path |
|---|---|
| `isas-104-student-room` | `via 133.11.179.39:41641` in **259 ms** — direct, not relayed |
| `aliyun-tokyo-server` | `via 47.79.148.225:41641` in 234 ms — direct |
| `isas-annex103-ap-room` | held a direct session while `tailscale status` ran (`tx/rx` moving) |

So a NAT in the campus path is not a wall: outbound from AWS, the host completed UDP hole-punching to
more than one machine behind it. Tailscale has no initiator/responder asymmetry anyway — once a machine
is on the tailnet, any other peer may open a connection to it, and the gateway only ever *initiates*.

**What is actually missing: no lab GPU machine is on the tailnet.** The peer list carries the lab's
rooms and a few laptops — `isas-104-student-room`, `isas-204-pi-room`, `isas-217-pcinfo-room`,
`isas-annex103-ap-room`, `isas-annex104-staff-room`, `isas-plantfactory`, `xanves`,
`higashimurayama-203`, `aliyun-tokyo-server` — and no node named `lab-pc` or resembling a GPU host.
That list is routers, though, not machines: the machines behind them need no node of their own.
`nerv-linux` (`100.112.135.10`) is still listed and has now been offline 53 days, and it is reachable
at `192.168.209.228` regardless, so it remains the cheapest rehearsal candidate for a reason that no
longer involves its client.

Tailnet: `fieldphenomics.com` (`tail75f165.ts.net`), and **it is not ours alone** — `labvpn@`,
`labdevices@`, `guowei@` and `haozhou-wang@` all own nodes on it, and no peer carries a tag, so the
default policy of "everything reaches everything" is in force today.

## What this implies for the compute plane

- The gateway needs an **outbound** TCP connection to the backend's HTTP port on a tailnet-routable
  address — a node's `100.x`, or a private address inside an advertised `/24`, which is what the
  measurement above settles; the backend needs nothing exposed publicly, which is what
  [`../rules/project.md`](../rules/project.md) already requires.
- Publish the container to the tailnet only, and keep the container itself off the LAN:
  `docker run --gpus all -p 127.0.0.1:8000:8000 …` then
  `tailscale serve --bg --https=443 http://127.0.0.1:8000`. Binding the container straight to a `100.x`
  address is the trap: `docker` resolves that bind at container start, and `tailscale0` may not exist
  yet, so the container fails on boot. `tailscale serve` is tailnet-only unless `funnel` is asked for,
  and it also stops the model port from appearing on the lab LAN.
- Tags and an ACL are owed before the first backend joins, per `project.md`: the tailnet carries other
  people's devices, and today nothing separates them; the per-backend token is the second lock, not the
  first.
- Latency to campus over this overlay measures 234–259 ms direct, which is the physical Tokyo→Frankfurt
  floor, so routing through the relay costs little latency but a shared relay costs throughput and
  jitter — relevant for image payloads. Whether a given lab machine punches through directly is one
  `tailscale ping` after it joins.

## Note on reading `tailscale ping`

A `tail -1` of its output shows `direct connection not established`, which reads as "relayed". That line
is a trailing statement of the *no-relay* case; the answer is on the line above it,
`pong from … via <address>:41641 in <ms>`. Read the pong line.

## Also found

`higashimurayama-203` (`100.122.162.91`) is online with `ExitNodeOption = True`, i.e. already approved
as an exit node — the non-campus egress that
[the campus path work](2026101513_host-unreachable-from-the-utokyo-network.md) was looking for.
