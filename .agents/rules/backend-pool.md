# Backend Pool Rules

The approved backend inventory and its private access constraints. Bringing one up is
[`../../deploy/gpu-backends.md`](../../deploy/gpu-backends.md); repo-wide invariants are in
[`project.md`](project.md) and are not repeated here.

- **This table is the repository's one explicit inventory exception, and it names machines by
  alias.** Keep symbolic machine/router labels here; only ignored `.agents/local/pool.md` resolves
  them to real addresses, node names and logins. A public clone must resolve no backend.
  [Private-coordinate rule](project.md#security-and-admission).
- **Reach backends through the gateway host's accepted router LAN routes.** Room routers advertise
  private subnets; no backend port may be forwarded publicly. This topology is not proof that other
  overlay peers are isolated: the service credential remains mandatory. The measured paths are in
  [`references/2026101610_backend-reachability-over-the-room-routers.md`](../references/2026101610_backend-reachability-over-the-room-routers.md).
- **Run models in containers and leave overlay routing at room routers.** Publish each model's
  assigned host port on its LAN interface; do not require a tailscale client or unmanaged model
  process on the GPU host. [Run lines](../../deploy/gpu-backends.md#32-the-run-line).
- **A rented `eu-central-1` node would arrive by the second path** — the gateway's own VPC and
  security group, without a tailnet dependency. Do not assume that unbuilt path exists.
  [Deferred deployment](../plans/2026101609_gpu-compute-plane.md).
- **Require a request credential, not a caller-address allowlist.** NAT and Docker proxy paths
  change the visible source; rebuilding the gateway must not change backend authorization.
  [Path measurements](../references/2026101621_deploying-sam31-on-the-5090.md).
- **Set the container's machine label on its run line.** `SAM31_MACHINE` supplies stable
  `computed_by.machine`; a random container hostname is not reproducible provenance.
  [Run configuration](../../deploy/gpu-backends.md#32-the-run-line).
- **A machine is a backend only when it appears here and in the gateway's `models.json`.** The table
  is what may serve; the list supplies routing candidates, which the gateway scores by expected wait.
- **Assign one host port per model consistently across machines.** `12711` is `sam3.1-meta`,
  `12712` is `gwfss-segformer-b4`; internal container ports remain `12711` in separate namespaces.
  [Port assignment](../../deploy/gpu-backends.md).
- **Keep retired rows, with dated last-observed status and evidence.** Missing evidence or empty
  cells mean unknown, not absent; never delete a machine's history by removing its row.
  [Inventory evidence](../references/2026101923_gpu-occupancy.md).
- **Include router, uplink and power in fault-domain reasoning.** A room subnet shares its router;
  an exit node shared with GPU access is part of the same failure chain.
  [Topology](../references/2026101610_backend-reachability-over-the-room-routers.md).
- **Enforce card capacity minus 1 GB on the run line.** The reserve protects the workstation's
  desktop and applies across its workloads, not independently per container.
  [Admission](project.md#architecture-and-identity).

## The machines

| Machine (alias) | Where | Router and LAN | GPU / VRAM | Location group | Last recorded status and evidence |
|---|---|---|---|---|---|
| `lab-3090` — host `labpc-host`, tailnet `labpc-tailnet` | laboratory | `room-router-a` (GL-BE9300), the laboratory LAN | RTX 3090, 24576 MiB | laboratory | serving — [occupancy 2026-10-19](../references/2026101923_gpu-occupancy.md) |
| `homepc-4090` | home | `home-router` (GL-MT6000), the home LAN | RTX 4090, 24564 MiB | home | serving — [occupancy 2026-10-19](../references/2026101923_gpu-occupancy.md) |
| `lab-5090` | laboratory | `room-router-d` (GL-MT6000), the 5090's LAN | RTX 5090, 32607 MiB | laboratory | serving; the largest card, and the only Windows host — 2026-10-19, [occupancy](../references/2026101923_gpu-occupancy.md) |
| a second 5090 | borrowed from a company | ? | RTX 5090, 32 GB | likely laboratory | not delivered — as recorded 2026-10-17 |
| ~3 more 90-class | laboratory | ? | unknown, ~24 GB | laboratory | not measured |
| a rented instance | `eu-central-1` | its security group — same VPC as the gateway, so no tailnet | L4 or L40S, 24 GB or 48 GB | AWS | not created |
| ETH hosts | Zurich | not negotiated | unknown | ETH | not negotiated |

Location groups are conservative labels, not proven independent fault domains. Separate laboratory
routers do not establish separate uplinks or power; verify those dependencies before claiming
independence. Model membership and current availability come from host configuration and the API,
not this last-observed hardware inventory.

**Re-measure resident memory before using a card's declaration as a placement reference.**
Do not assume a measurement copied from another GPU describes this one.
Evidence:
[`references/2026101621_deploying-sam31-on-the-5090.md`](../references/2026101621_deploying-sam31-on-the-5090.md).
