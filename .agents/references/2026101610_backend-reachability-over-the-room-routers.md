# The room routers carry the backends, and a closed port is not an unreachable host

Date: 2026-10-16 10:10 JST.
Topic: whether the gateway host reaches the two candidate GPU machines on their private addresses
when neither runs a tailscale client, what advertises those addresses, and the two conclusions that
change the compute plane: an on-prem backend is a container and nothing else, and `2026101514`'s
inventory of what is on the tailnet needs correcting.

## The question

Can `biohackathon-api-server` (the EC2 host, Frankfurt) reach a laboratory GPU machine and a home GPU
machine by their plain private addresses, given that neither machine runs a tailscale client — and if
so, what has to be true for a third machine to join the same way?

## Answer: both, at the IP layer and at the port layer

Measured 2026-10-16 from the host, over the tailnet, against `192.168.209.228` (home, 4090) and
`192.168.250.228` (laboratory, 3090):

| Target | ICMP | TCP |
|---|---|---|
| `192.168.209.228` | 242–250 ms, 0% loss | **22 open, 22000 open**; 2233 and 8771 refused |
| `192.168.250.228` | 337–350 ms, 0% loss | **2233, 8771, 22000 open**; 22 refused |

The differing port sets are what makes the result trustworthy: a middlebox answering everything would
have opened all six. Each set matches what the machine itself reports — `ss -lnt` on
`192.168.250.228` lists `0.0.0.0:2233`, `0.0.0.0:8771` and `*:22000`, and no `sshd` at all.

**So the laboratory machine's refused 22 meant nothing about reachability.** It was the first port
probed, it refused, and the tempting reading — "the campus NAT blocks this direction after all" — was
wrong. The kernel answered ICMP from the same address, and three other ports were open.

## What carries them: one GL.iNet router per `/24`

`tailscale status --json` on the host, read for `AllowedIPs`:

| Advertised | HostName | Node | Tailnet address |
|---|---|---|---|
| `192.168.209.0/24` | `GL-MT6000` | `higashimurayama-203` | `100.122.162.91` — also the advertised **exit node** |
| `192.168.250.0/24` | `GL-BE9300` | `isas-annex103-ap-room` | `100.69.177.30` |
| `192.168.251.0/24` | `GL-MT6000` | `isas-annex104-staff-room` | `100.114.21.126` |
| `192.168.252.0/24` | `GL-MT6000` | `isas-104-student-room` | `100.95.165.86` |
| `192.168.253.0/24` | `GL-MT6000` | `isas-217-pcinfo-room` | `100.89.84.22` |
| `192.168.254.0/24` | `GL-MT6000` | `isas-plantfactory` | `100.78.79.84` |
| `192.168.233.0/24` | `GL-MT6000` | `isas-204-pi-room` | `100.106.231.96` |

The host advertises nothing of its own. Every node named after a room, and the one named
`higashimurayama-203`, is a **router**, not a machine: each advertises exactly one `/24` and forwards
for everything behind it. `install-tailscale.sh` already puts the host in `--accept-routes`, so those
`/24`s are routable from the host without further work.

**Consequence: a machine joins the compute plane by being plugged into one of those LANs and running
a container.** No tailscale client, no account, no key, no tag on the machine. The subnet route is
what makes it reachable, and it already exists.

## `nerv-linux` needs no client — it is one of these machines

The tailnet still lists `nerv-linux` (`100.112.135.10`, `haozhou-wang@`) as a node, **offline 53
days**: the client that was installed on that machine is not what makes it reachable any more. The
machine is at `192.168.209.228`, behind `higashimurayama-203`, and it answers the host on that address
today. `2026101514` recorded it as "a cheap first backend to rehearse with" precisely because it was
an onboarded node; the accurate statement is that it is the same rehearsal candidate for a different
reason, and no client has to be started to use it.

## Fault domains: per router, not per machine

Since reachability arrives through one router per `/24`, the failure domains are too:

- Machines inside one `/24` share a router, its uplink, and the power behind it. Three laboratory
  machines are **one** domain, and a router reboot removes all three.
- The home `/24` and the laboratory `/24` share none of those. **They are two independent domains**,
  so on-prem satisfies a two-domain target on its own; a rented instance would be a third.

That corrects the plan's earlier assumption that a second independent domain had to be bought.

## What to do differently next time

- **Never conclude unreachability from a refused port.** Ask the target what it listens on
  (`ss -lnt`) or probe a port known to be open; a refused first probe proves only that nothing was
  there.
- **Read subnet routes from `tailscale status --json`.** The routes live in a separate routing table
  (`ip route get <ip>` reports `dev tailscale0 table 52`), so a plain `ip route` lists none of them
  and reads as "no subnet routes are advertised". That inspection produced exactly the wrong
  conclusion before `ip route get` was tried.
- **Reach the host at its tailnet address.** `ssh -i ~/.ssh/gwhs-demo.pem admin@100.88.197.115`
  worked from the laboratory LAN; the public Elastic IP refused 22 from the same network, which is
  the destination-specific block recorded in
  [`2026101513_host-unreachable-from-the-utokyo-network.md`](2026101513_host-unreachable-from-the-utokyo-network.md).

To re-run: on the host, `tailscale status --json` for the advertisements, then
`ip route get <target>` and a TCP probe per port.

## Also found

- **The tailnet's tags are still absent**, as `2026101514` recorded, and the peer list now also shows
  `labvpn@` and `guowei@` nodes. A tailnet ACL is a single file for the whole tailnet, so tightening
  it rewrites four other owners' policy — which is why the plan accepts the exposure for the event
  and records it instead.
- **Latency now has two numbers rather than one**: home 242–250 ms and laboratory 337–350 ms to
  Frankfurt. The laboratory path is both slower and, in one two-packet ping from the laboratory
  machine *to* the host, much jitterier (max 1240 ms, mdev 472 ms). Two packets is not a measurement
  of jitter, only a reason to measure it properly before promising a latency budget.
