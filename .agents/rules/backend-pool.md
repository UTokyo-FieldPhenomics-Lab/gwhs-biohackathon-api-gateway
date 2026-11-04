# Backend Pool Rules

The approved backend inventory and its private access constraints. Bringing one up is
[`../../deploy/gpu-backends-local.md`](../../deploy/gpu-backends-local.md) for an on-prem machine and
[`../../deploy/gpu-backends-aws.md`](../../deploy/gpu-backends-aws.md) for a rented instance;
repo-wide invariants are in [`project.md`](project.md) and are not repeated here.

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
  process on the GPU host. [Run lines](../../deploy/gpu-backends-local.md#32-the-run-line).
- **A managed-API family may share one loopback port with path prefixes.** One process on the gateway
  host serves `nano-banana-2`, `nano-banana-2.1` and `nano-banana-pro` on `127.0.0.1:8010`, each
  prefix its own base URL and declaration, because one process owns one upstream quota and one
  ledger. A GPU container keeps one host port per model.
  [Runbook](../../deploy/nanobanana.md).
- **A rented `eu-central-1` node arrives by the second path** — the gateway's own VPC and security
  group, without a tailnet dependency. The path is built and rehearsed, but no instance runs by
  default: a row below is a machine only while its instance exists.
  [Rented path](../../deploy/gpu-backends-aws.md), [rehearsal](../references/20261027_aws-gpu-backend.md).
- **Require a request credential, not a caller-address allowlist.** NAT and Docker proxy paths
  change the visible source; rebuilding the gateway must not change backend authorization.
  [Path measurements](../references/2026101621_deploying-sam31-on-the-5090.md).
- **Set the container's machine label on its run line.** `SAM31_MACHINE` supplies stable
  `computed_by.machine`; a random container hostname is not reproducible provenance.
  [Run configuration](../../deploy/gpu-backends-local.md#32-the-run-line).
- **A machine is a backend only when it appears here and in the gateway's `models.json`.** The table
  is what may serve; the list supplies routing candidates, which the gateway scores by expected wait.
- **Assign one host port per model consistently across machines.** `12711` is `sam3.1-meta`,
  `12712` is `gwfss-segformer-b4`, `12713` is `tasselnetv4`, `12714` is `dinov3-vitl16`, `12715` is
  `dinov3-vith16plus`, `12716` is `internvl-u`, `12717` is `yolo26-ultralytics`, `12718` is
  `yolo26-seg-ultralytics`, `12719` is `sprout-l-gwfss`; internal container ports remain `12711` in
  separate namespaces.
  [Port assignment](../../deploy/gpu-backends-local.md).
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
| `lab-3090` — host `labpc-host`, tailnet `labpc-tailnet` | laboratory | `room-router-a` (GL-BE9300), the laboratory LAN | RTX 3090, 24576 MiB | laboratory | serving all five, plus `yolo26-ultralytics` on `12717`, `yolo26-seg-ultralytics` on `12718` — 2026-11-03, [the pair](../references/2026110316_yolo-ultralytics-pair.md), and `sprout-l-gwfss` on `12719` — 2026-11-04, [the model](../references/2026110420_sprout-l-gwfss.md); `tasselnetv4` added on `12713` — 2026-10-30, [counting contract](../references/2026102923_tasselnetv4-counting-contract.md); `dinov3-vitl16` and `dinov3-vith16plus` added on `12714`/`12715` — 2026-11-02, [backbones](../references/2026110215_dinov3-backbones.md); path 0.0 ms — 2026-10-29, [egress loss](../references/202610291100_campus-egress-loss.md) |
| `homepc-4090` | home | `home-router` (GL-MT6000), the home LAN | RTX 4090, 24564 MiB | home | serving `gwfss`, `tasselnetv4` (`12713`, added 2026-10-30, [counting contract](../references/2026102923_tasselnetv4-counting-contract.md)) and `internvl-u` (`12716`, added 2026-11-03, [prose and pictures](../references/2026110219_internvl-u-prose-and-picture-answers.md)); path 22–537 ms, mdev 163, ICMP flapping — 2026-10-29, [egress loss](../references/202610291100_campus-egress-loss.md) |
| `lab-5090` | laboratory | `room-router-d` (GL-MT6000), the 5090's LAN | RTX 5090, 32607 MiB | laboratory | serving all three, and the only Windows host; `tasselnetv4` added on `12713` — 2026-10-30, [counting contract](../references/2026102923_tasselnetv4-counting-contract.md); path 1.9–2.6 ms, ICMP filtered by its own firewall — 2026-10-29, [egress loss](../references/202610291100_campus-egress-loss.md) |
| `lab-intel3090` | laboratory | `room-router-e` (GL-MT6000), the plant-factory LAN | RTX 3090, 24576 MiB | laboratory | serving all three on `12711`–`12713` — 2026-11-02, [bring-up](../references/2026110214_adding-lab-intel3090.md); alive on all three in `/pool`, rtt 0.58 s; Windows 11, so it serves only while its user session is signed in (per-user Docker Desktop, no automatic sign-in) |
| a second 5090 | borrowed from a company | ? | RTX 5090, 32 GB | likely laboratory | not delivered — as recorded 2026-10-17 |
| ~3 more 90-class | laboratory | ? | unknown, ~24 GB | laboratory | not measured |
| `aws-euc1-l4` — EC2 `g6.xlarge` in the gateway's VPC | AWS `eu-central-1` | the gateway's subnet; group `biohack-gpu-backend`, `22` and `12711`–`12713` from the host's group only | NVIDIA L4, 23034 MiB | AWS | not running — the artifacts are a base AMI and a digest, not a machine; last served 2026-10-27 ([rehearsal](../references/20261027_aws-gpu-backend.md)) |
| `vertex-global` — the managed-API wrapper on `gwhs-host` | the gateway host; Google global through its egress | loopback `127.0.0.1:8010`, prefixes `nano-banana-2`, `nano-banana-2.1`, `nano-banana-pro` | no GPU | AWS | implemented 2026-11-03, no container runs yet — [runbook](../../deploy/nanobanana.md) |
| ETH hosts | Zurich | not negotiated | unknown | ETH | not negotiated |

Location groups are conservative labels, not proven independent fault domains. Separate laboratory
routers do not establish separate uplinks or power; verify those dependencies before claiming
independence. Model membership and current availability come from host configuration and the API,
not this last-observed hardware inventory.

**Re-measure resident memory before using a card's declaration as a placement reference.**
Do not assume a measurement copied from another GPU describes this one.
Evidence:
[`references/2026101621_deploying-sam31-on-the-5090.md`](../references/2026101621_deploying-sam31-on-the-5090.md).
