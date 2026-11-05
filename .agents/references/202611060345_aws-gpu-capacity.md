# What Frankfurt will and will not give us, and when

**2026-11-06, 02:35–03:45 JST.** Probes that decide whether the event's rented card can be a g7e, an
L4, or nothing. The allocation this feeds is
[`plans/202611060350_gpu-allocation.md`](../plans/202611060350_gpu-allocation.md).

## The question, and why a launch is the only way to ask it

`servicequotas:GetServiceQuota` and `ListServiceQuotas` are denied for `biohack-gpu-operator`, so the
limit cannot be read. `describe-instance-type-offerings` says only that a type exists somewhere in the
region. What decides a deployment is *capacity in an AZ at this hour*, and the only API that answers it
is `RunInstances`:

- `VcpuLimitExceeded` — the quota is the obstacle.
- `InsufficientInstanceCapacity` — the quota is fine and the AZ is empty.
- an instance id — both are fine.

An instance that launches is terminated within seconds. The whole session cost about $0.01.

## What came back

| type | 1a | 1b | 1c (the gateway's AZ) |
|---|---|---|---|
| `g7e.2xlarge` (96 GB) | no capacity | no capacity | **type not offered in this AZ** |
| `g7e.4xlarge` (96 GB) | no capacity | no capacity | type not offered |
| `g7e.2xlarge`, spot | no capacity | — | — |
| `g6.xlarge` (L4), 18:40 CET | no capacity | no capacity | no capacity |
| `g6.xlarge` (L4), 19:41 CET | no capacity | **launched** | **launched** |
| `g6e.xlarge` (L40S) | no capacity | — | no capacity |
| `g4dn.xlarge` (T4) | — | — | **launched** |

Three facts worth keeping:

- **The quota is not the obstacle.** Every refusal but one was `InsufficientInstanceCapacity`; the
  exception is the AZ that does not carry the type. The account's G/VT on-demand limit is 32 vCPU
  (case `179211256400789`), which covers a `g7e.2xlarge` (8) or a `g7e.4xlarge` (16).
- **`g7e` is absent from `eu-central-1c` entirely** — the AZ the gateway's subnet sits in — and sold
  out in `1a`/`1b` at every size, on demand and on spot. It is a Frankfurt-evening picture taken on one
  day; it is not a promise about December.
- **L4 capacity moves within the hour.** `g6.xlarge` was unavailable in all three AZs at 18:40 CET and
  available in two of them at 19:41 CET, same account, same subnets, same AMI — and unavailable again at
  19:43, two minutes later. A probe is a snapshot, not a property of the region.

## The AZ question

`1a` and `1b` are the same VPC as the gateway, so they are private: `172.31.16.0/20` and
`172.31.32.0/20` beside the gateway's `172.31.0.0/20`, on the VPC's own `local` route. No public
address, no NAT, no internet gateway. A security group that names the gateway's group as its source
keeps working, because group references are VPC-wide.

What it costs to be one AZ over: 0.3–1 ms of latency (noise beside the Starlink path at tens of ms) and
$0.01/GB each way for cross-AZ transfer — a 1.5 MB internvl picture is 1.5 GB per thousand calls, about
three cents. An instance that changes AZ changes its private address, so `models.json` follows it.

## The probe, and its schedule

[`deploy/probe-gpu-capacity.sh`](../../deploy/probe-gpu-capacity.sh) tries four types across the three
subnets, logs one line per attempt to `~/.local/state/gwhs/gpu-capacity.log`, and terminates anything
it launches. The subnet ids, the AMI and the security group are host-private and are not in the script:
it sources `~/.config/gwhs/gpu-probe.env` (mode 0600), so the repository carries the question and the
machine carries the identifiers. A crontab entry on the workstation runs it hourly from 09:00 to 15:00
JST, which is 01:00–07:00 in Frankfurt:

    0 9-15 * * * /home/crest/Documents/Github/gwhs-biohackaton-api-gateway/deploy/probe-gpu-capacity.sh

The schedule is the point: capacity is a function of the hour, and the question that matters is what
Frankfurt's small hours look like, not what they look like while everyone is awake. A launch recorded
there says "possible", not "reserved" — if the event needs the card, it must be held from the moment it
appears, or covered by a Capacity Reservation, which can only be created while capacity exists.

## Lessons

- **A quota error and a capacity error are different diagnoses** and the same API tells them apart;
  never assume the quota case is the reason a launch failed.
- **Type availability and capacity are separate facts**: `describe-instance-type-offerings` listed
  `g7e.2xlarge` for the region while the gateway's own AZ does not carry it at all.
- **Probe, then terminate.** A successful probe costs cents; holding a card at the wrong moment costs
  dollars an hour and reserves nothing for December.
