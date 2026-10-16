# The compute plane's backends

Which machines can serve a model, what each can carry, and where the gateway's list of them lives.
This file is a **document**: no code reads it, so a stale row costs a wrong judgement and never a wrong
route. What the gateway actually reads is host configuration — [below](#the-list-the-gateway-reads).

Why a backend needs no tailscale client, and the measurements this table rests on, are in
[`.agents/references/2026101610_backend-reachability-over-the-room-routers.md`](../.agents/references/2026101610_backend-reachability-over-the-room-routers.md).
The contract a backend container implements is in
[`.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md).

## How a backend is addressed

Two things make a machine reachable, and neither is a client on the machine:

1. It sits on one of the LANs below. That LAN's **router is the tailnet node**, advertising its `/24`,
   and the gateway host accepts routes — so a private address inside the `/24` is reachable from
   Frankfurt with nothing installed on the machine.
2. A container listens on the port, bound to `127.0.0.1` and published with `tailscale serve`. Binding
   it to the tailnet interface directly is the trap: `docker` resolves that bind at start, `tailscale0`
   may not exist yet, and the container dies on boot.

Convention: the inference port is **8000**, which is what the plan's examples assume.

## The inventory

| Machine | Where | Advertised by | Private address | GPU | VRAM | Fault domain | Last measured |
|---|---|---|---|---|---|---|---|
| `labpc` | laboratory | `isas-annex103-ap-room` (GL-BE9300), `192.168.250.0/24` | `192.168.250.228` | RTX 3090 | 24 GB | laboratory | 2026-10-16 — TCP 2233/8771/22000 open, ICMP 337–350 ms, 0% loss |
| `homepc` | home | `higashimurayama-203` (GL-MT6000), `192.168.209.0/24` | `192.168.209.228` | RTX 4090 | 24 GB | home | 2026-10-16 — TCP 22/22000 open, ICMP 242–250 ms, 0% loss |
| one 5090 | laboratory | ? | ? | RTX 5090 | 32 GB | laboratory | not measured |
| a second 5090 | borrowed from a company | ? | ? | RTX 5090 | 32 GB | likely laboratory | not delivered |
| ~3 more 90-class | laboratory | ? | ? | unknown | ~24 GB | laboratory | not measured |
| a rented instance | `eu-central-1` | its security group — same VPC as the gateway, so no tailnet at all | assigned on launch | L4, or L40S | 24 GB, or 48 GB | AWS | not created |
| ETH hosts | Zurich | not negotiated | not negotiated | unknown | unknown | ETH | not negotiated |

Empty cells are **unknown, not absent** — fill them in when the machine is measured, and keep the row
when it stops existing rather than deleting it quietly.

## Fault domains

A domain is a router, its uplink, and the power behind it; machines inside one `/24` share all three.
So however many 90-class machines the laboratory has, they are **one** domain — a router reboot removes
all of them at once. Home is a **second**, genuinely independent one. A rented `eu-central-1` instance
would be a **third**, and the only one whose availability is contracted rather than borrowed.

Two things follow that are easy to miss:

- The home router is also the tailnet's advertised **exit node**, so losing it loses the egress
  workaround as well as the GPU behind it.
- The laboratory path measured about 100 ms worse than the home path to Frankfurt, and jitters more.

## What each machine can carry

Not a table but a rule, because the numbers come from measuring the models rather than the machines:

    admit            free VRAM ≥ resident + per_request
    max_in_flight    1 + floor((free − resident − per_request) / per_request)

`resident` and `per_request` are declared by the model's own container (its config and its image
label); the free figure comes from the machine at placement time. The measurement that produces them,
and the per-model resolution they hold at, is Task 1 of the plan. **A machine's row above does not
decide what it can serve** — that follows from the arithmetic.

## The list the gateway reads

**Not in this repository.** It names running machines, so it is host configuration, beside the token
pool and the environment file in `~/.config/biohack-gateway/` — the same reasoning as §1.5 of the
runbook. The shape, one entry per model:

    sam3.1:  ["http://192.168.250.228:8000"]
    yolo:    ["http://192.168.209.228:8000", "http://192.168.250.228:8000"]

The gateway tries a model's backends in order and marks a failed one unhealthy for 30 seconds. A
model's list is the **only** place its backends appear: each backend declares at startup which models
it serves and how many it can hold in flight, so capability is derived from that declaration and the
list rather than written down a second time here.

## Adding a machine

1. Put it on one of the LANs above. Nothing to install beyond Docker and the NVIDIA container toolkit.
2. Read its declaration and check the machine can take it — `docker image inspect` for the label,
   `nvidia-smi --query-gpu=index,memory.free --format=csv` for the cards.
3. Start it bound to `127.0.0.1`, then publish it with `tailscale serve`.
4. Prove it from the host, not from the machine: `nc -vz <private address> 8000`.
5. Add that address to the model's list on the host, and add this machine's row above.

## How to re-measure a row

```bash
# who advertises which /24 — the answer is in AllowedIPs, and only there
ssh -i ~/.ssh/gwhs-demo.pem admin@100.88.197.115 \
  'tailscale status --json' | python3 -c 'import json,sys; [print(a, v["HostName"], v["TailscaleIPs"][0]) for v in json.load(sys.stdin)["Peer"].values() for a in v["AllowedIPs"] if a.startswith("192.168")]'

# reachability, run on the host: the subnet routes live in their own table, so `ip route` shows none
ssh -i ~/.ssh/gwhs-demo.pem admin@100.88.197.115 \
  'ip route get 192.168.250.228; ping -c 3 192.168.250.228'
```

A refused port is not an unreachable host — ask the machine what it listens on (`ss -lnt`) before
concluding anything about the path.
