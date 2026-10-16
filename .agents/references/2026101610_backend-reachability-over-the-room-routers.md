# The room routers carry the backends, and a closed port is not an unreachable host

Date: 2026-10-16 10:10 JST, carrying the tailnet inventory first read on 2026-10-15 14:25.
Topic: whether the gateway host reaches the two candidate GPU machines on their private addresses
when neither runs a tailscale client, what advertises those addresses, what a backend therefore has
to do, and which machine could serve as the first one. It replaces
`2026101514_tailnet-topology-and-lab-reachability.md`; that file's measurements and its `tailscale
ping` pitfalls are folded in below.

## The question

Can `biohackathon-api-server` (the EC2 host, Frankfurt) reach a laboratory GPU machine and a home GPU
machine by their plain private addresses, given that neither machine runs a tailscale client — and if
so, what has to be true for a third machine to join the same way?

## Answer: both, at the IP layer and at the port layer

Measured 2026-10-16 from the host, over the tailnet, against `homepc-4090` (home, 4090) and
`lab-3090` (laboratory, 3090):

| Target | ICMP | TCP |
|---|---|---|
| `homepc-4090` | 242–250 ms, 0% loss | **22 open, 22000 open**; 2233 and 8771 refused |
| `lab-3090` | 337–350 ms, 0% loss | **2233, 8771, 22000 open**; 22 refused |

The differing port sets are what makes the result trustworthy: a middlebox answering everything would
have opened all six. Each set matches what the machine itself reports — `ss -lnt` on
`lab-3090` lists `0.0.0.0:2233`, `0.0.0.0:8771` and `*:22000`, and no `sshd` at all.

**So the laboratory machine's refused 22 meant nothing about reachability.** It was the first port
probed, it refused, and the tempting reading — "the campus NAT blocks this direction after all" — was
wrong. The kernel answered ICMP from the same address, and three other ports were open.

## Direction was never the obstacle

The 10-15 read settled this before the addresses did, with `tailscale ping` from the host:

| Peer | Path |
|---|---|
| `room-router-c` | `via the campus client network:41641` in **259 ms** — direct, not relayed |
| `aliyun-tokyo-server` | `via a client:41641` in 234 ms — direct |
| `room-router-a` | held a direct session while `tailscale status` ran (`tx`/`rx` moving) |

Outbound from AWS, the host completes UDP hole-punching to machines behind the campus NAT, and
Tailscale has no initiator/responder asymmetry — once a machine is on the tailnet any peer may open a
connection to it, and the gateway only ever *initiates*. What was missing was never a direction; it is
that the pool publishes on its router's LAN, which the next section measures.

## What carries them: one GL.iNet router per `/24`

`tailscale status --json` on the host, read for `AllowedIPs`:

| Advertised | HostName | Node | Tailnet address |
|---|---|---|---|
| `the home LAN` | `GL-MT6000` | `home-router` | `home-router` — also the approved **exit node** |
| `the laboratory LAN` | `GL-BE9300` | `room-router-a` | `room-router-a` |
| `a room LAN` | `GL-MT6000` | `room-router-b` | `room-router-b` |
| `a room LAN` | `GL-MT6000` | `room-router-c` | `room-router-c` |
| `the 5090's LAN` | `GL-MT6000` | `room-router-d` | `room-router-d` |
| `a room LAN` | `GL-MT6000` | `room-router-e` | `room-router-e` |
| `a room LAN` | `GL-MT6000` | `room-router-f` | `room-router-f` |

The host advertises nothing of its own. Every node named after a room, and the one named
`home-router`, is a **router**, not a machine: each advertises exactly one `/24` and forwards
for everything behind it. `install-tailscale.sh` already puts the host in `--accept-routes`, so those
`/24`s are routable from the host without further work.

The 10-15 read listed `room-router-c`, `room-router-f`, `room-router-d`,
`room-router-a`, `room-router-b`, `room-router-e`, `xanves`,
`home-router` and `aliyun-tokyo-server` and treated the missing `lab-pc`/`home-pc` nodes as an
onboarding debt. The names are right and the reading was not: the rooms are routers, `xanves` and the
laptops are not backends, and `aliyun-tokyo-server` is a third machine in Tokyo.

**Consequence: a machine joins the compute plane by being plugged into one of those LANs and running
a container.** No tailscale client, no account, no key, no tag on the machine. The subnet route is
what makes it reachable, and it already exists.

## `labpc-tailnet` needs no client — it is one of these machines

The tailnet still lists `labpc-tailnet` (`labpc-tailnet`, `haozhou-wang@`) as a node, **offline 53
days**: the client that was installed on that machine is not what makes it reachable any more. The
machine is at `homepc-4090`, behind `home-router`, and it answers the host on that address
today. The 10-15 read called it "a cheap first backend to rehearse with" precisely because it was an
onboarded node; the accurate statement is that it is the same rehearsal candidate for a different
reason, and no client has to be started to use it.

## Fault domains: per router, not per machine

Since reachability arrives through one router per `/24`, the failure domains are too:

- Machines inside one `/24` share a router, its uplink, and the power behind it. Three laboratory
  machines are **one** domain, and a router reboot removes all three.
- The home `/24` and the laboratory `/24` share none of those. **They are two independent domains**,
  so on-prem satisfies a two-domain target on its own; a rented instance would be a third.

That corrects the plan's earlier assumption that a second independent domain had to be bought.

## What a backend has to do

- **Publish the container on the LAN and nothing else.** The gateway needs one outbound TCP connection
  to the backend's HTTP port on a tailnet-routable address — a node's `100.x`, or a private address
  inside an advertised `/24`; the backend exposes nothing publicly, which is what
  [`../rules/project.md`](../rules/project.md) already requires. `docker run -p 8000:8000` (bound
  `0.0.0.0`) is the whole contract, measured working on all three machines, the Windows one through
  Docker Desktop's Windows-side proxy. `tailscale serve --bg --https=443 http://127.0.0.1:8000` behind
  `-p 127.0.0.1:8000:8000` is the tailnet-only alternative for a machine that is its own node; it is
  not what this pool does, because it costs a client per machine for a guarantee the event has already
  accepted giving up.
- **Never bind a container straight to a `100.x` address.** `docker` resolves that bind at start and
  `tailscale0` may not exist yet, so the container fails at boot.
- **Tags and an ACL are owed before the first backend joins**, per `project.md`: the tailnet carries
  other people's devices and nothing separates them. The per-backend token is the second lock, not the
  first. `deploy/README.md` §1.3 records the event's decision to deviate — one ACL file rewrites four
  other owners' policy — so the invariant stands and the exception is written down. The inventory and
  the per-OS bring-up recipes are in [`../../deploy/gpu-backends.md`](../../deploy/gpu-backends.md).
- **Latency is Tokyo→Frankfurt**, 234–259 ms host→campus direct and 242–350 ms campus→host, so a relay
  costs little latency but a shared relay costs throughput and jitter — relevant for image payloads.

## Reading `tailscale ping`

A `tail -1` of its output shows `direct connection not established`, which reads as "relayed". That
line is a trailing statement of the *no-relay* case; the answer is on the line above it,
`pong from … via <address>:41641 in <ms>`. Read the pong line.

## The room leg, when a room cannot reach the host

Tailnet SSH from a room stops working while the Tailscale console reports the node **online**, with key
expiry already disabled. That is not a firewall: a room's packets arrive carrying the client's own LAN
address, and the host answers them only if it accepts that room's subnet route — which is exactly what
`--accept-routes` on the host and the advertised `/24`s above provide. Two checks tell the legs apart:
from the room, `ping` the room router's *own* tailnet address, and on the host,
`ip -s link show tailscale0` while that ping runs (a flat `RX` means the packets never arrived). The
constraint is in [`../rules/deploy.md`](../rules/deploy.md). A room's SSH is relayed and therefore
slow; nothing bulk should cross it.

## What to do differently next time

- **Never conclude unreachability from a refused port.** Ask the target what it listens on
  (`ss -lnt`) or probe a port known to be open; a refused first probe proves only that nothing was
  there.
- **Read subnet routes from `tailscale status --json`.** The routes live in a separate routing table
  (`ip route get <ip>` reports `dev tailscale0 table 52`), so a plain `ip route` lists none of them
  and reads as "no subnet routes are advertised". That inspection produced exactly the wrong
  conclusion before `ip route get` was tried — and the same table, holding a route for the *client's*
  prefix, is what once made the host look unreachable from the whole campus
  ([`2026101513_host-unreachable-from-the-utokyo-network.md`](2026101513_host-unreachable-from-the-utokyo-network.md)).
- **Reach the host at its tailnet address.** `ssh -i ~/.ssh/gwhs-demo.pem <user>@gwhs-host` worked
  from the laboratory LAN.

To re-run: on the host, `tailscale status --json` for the advertisements, then `ip route get <target>`
and a TCP probe per port.

## Also found

- **The tailnet's tags are still absent**, as the 10-15 read recorded, and the peer list now also shows
  `labvpn@` and `guowei@` nodes. A tailnet ACL is a single file for the whole tailnet, so tightening it
  rewrites four other owners' policy — which is why the plan accepts the exposure for the event and
  records it instead.
- **Latency has two numbers rather than one**: home 242–250 ms and laboratory 337–350 ms to Frankfurt.
  The laboratory path is both slower and, in one two-packet ping from the laboratory machine *to* the
  host, much jitterier (max 1240 ms, mdev 472 ms). Two packets is not a measurement of jitter, only a
  reason to measure it properly before promising a latency budget.
- **`home-router` is an approved exit node** (`ExitNodeOption = True`), i.e. a non-campus
  egress already exists if one is ever needed for a check.
