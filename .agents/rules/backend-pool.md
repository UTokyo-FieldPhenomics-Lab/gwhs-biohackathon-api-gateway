# Backend Pool Rules

The machines that serve a model, and the only path that reaches them. Bringing one up is
[`../../deploy/gpu-backends.md`](../../deploy/gpu-backends.md); repo-wide invariants are in
[`project.md`](project.md) and are not repeated here.

- **The pool is reachable from one network, through one host, and from nowhere else.** Each room
  router is a tailnet node advertising its own `/24`, and the gateway host is the only machine that
  accepts those routes — so a backend's private address resolves from that host and from no other
  machine, in or out of the campus. There is no public address and no port forwarded to the internet,
  and **an address in the table below means nothing outside UTokyo Field Phenomics Lab**: a reader
  elsewhere who copies one gets a timeout, not a machine.
- **What answers is a container publishing `12711` on the LAN interface.** The machine runs no
  tailscale client and no model process of its own; the route is the router's job and the container
  is the backend.
- **A rented `eu-central-1` node would arrive by the second path** — the gateway's own VPC and
  security group, no tailnet at all. That path is designed and not built
  ([plan](../plans/2026101609_gpu-compute-plane.md)); it is the one that does not borrow a room's
  availability.
- **A backend is scoped by a credential carried in the request, never by the caller's address.** What
  a backend sees of its caller is a fact about the path — one room's router passes the caller's
  tailnet address, another rewrites it to its own `the 5090's own proxy`, and Docker Desktop's port proxy is
  a hop of its own — so an allowlist would hold on one machine and silently not on the next, and it
  would bind a backend to a gateway address this design wants disposable. The measurements are in
  [`references/2026101621_deploying-sam31-on-the-5090.md`](../references/2026101621_deploying-sam31-on-the-5090.md).
- **Every container names the machine it runs on.** `SAM31_MACHINE` is set on the run line, because
  every answer carries that name in `computed_by` and a container's own host name is a random id. The
  name is what makes two answers that differ traceable to two cards; without it a record names a card
  and no host.
- **A machine is a backend only when it appears here and in the gateway's `models.json`.** The table
  is what may serve; the list is what does serve, in order.
- **A model's host port is the model's, the same number on every machine.** `12711` is `sam3.1-meta`
  wherever it runs and `12712` is `gwfss-segformer-b4`, so an address names the model and the host
  without a third field to keep true. Inside a container the port stays `12711` for both: each has
  its own network namespace, and the host-side number is the one that routes. A port that meant one
  model here and another there would make a wrong `-p` silent until the first call, and would make
  an address in a record unreadable without knowing which machine wrote it.
- **This table is current state, and the measurements behind it are references.** Fill a row in when
  a machine is measured, keep the row when the machine retires and change its status, and never
  delete one: a machine that disappears from here is a machine the next person reconfigures blind.
  An empty cell is unknown, not absent.
- **A fault domain is a router, its uplink, and the power behind it**, so every machine in one `/24`
  shares one. The home router is also the tailnet's advertised **exit node**, so it is the egress
  workaround and a GPU in one failure. Both facts, with the latencies and what each router was
  measured to do, are in
  [`references/2026101610_backend-reachability-over-the-room-routers.md`](../references/2026101610_backend-reachability-over-the-room-routers.md).
- **A card is admitted against the card minus 1 GB, never against the card.** These are
  workstations whose desktop drives the same display, so 24 GB is placed against 23 GB and 32 GB
  against 31 GB. The limit is imposed by the run line rather than trusted, and it is the same
  arithmetic whether the container is a model or anything else added later.

| Machine | Where | Router and LAN | Private address | GPU / VRAM | Fault domain | Status |
|---|---|---|---|---|---|---|
| `labpc` (3090) — host `labpc-host`, tailnet `labpc-tailnet` | laboratory | `room-router-a` (GL-BE9300), `the laboratory LAN` | `lab-3090` | RTX 3090, 24576 MiB | laboratory | serving; no SSH access (`sshd` refuses 22) |
| `homepc` (4090) | home | `home-router` (GL-MT6000), `the home LAN` | `homepc-4090` | RTX 4090, 24564 MiB | home | serving `sam3.1-meta` as `homepc-4090`, in `models.json` since 17 October 2026 — the pool's third backend |
| lab 5090 (Windows 11) | laboratory | `room-router-d` (GL-MT6000), `the 5090's LAN` | `lab-5090` | RTX 5090, 32607 MiB | laboratory | serving; the largest card, and the only Windows host |
| a second 5090 | borrowed from a company | ? | ? | RTX 5090, 32 GB | likely laboratory | not delivered |
| ~3 more 90-class | laboratory | ? | ? | unknown, ~24 GB | laboratory | not measured |
| a rented instance | `eu-central-1` | its security group — same VPC as the gateway, so no tailnet | assigned on launch | L4 or L40S, 24 GB or 48 GB | AWS | not created |
| ETH hosts | Zurich | not negotiated | not negotiated | unknown | ETH | not negotiated |

The 5090's **resident** figure is the 3090's: the container declares `resident_gb 3.7` from a
3812 MiB measurement on the 3090, while on the 5090 the same container left roughly 6.5 GB resident
and a larger CUDA context. Admission is unaffected (31 GB budget against 5.5 GB requested), but the
declaration is what a human reads before starting a container, so re-measure it on this card before
it becomes the machine the others are placed against — the numbers are in
[`references/2026101621_deploying-sam31-on-the-5090.md`](../references/2026101621_deploying-sam31-on-the-5090.md).
