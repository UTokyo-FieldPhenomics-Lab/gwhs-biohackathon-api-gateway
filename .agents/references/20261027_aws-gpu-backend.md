# The rented backend, rehearsed: one L4, one image, one afternoon

Date: 2026-10-27. Topic: bringing up the first backend on rented AWS compute, what it cost in wall
clock and dollars, and why the artifact is now a base image plus a digest rather than a machine.

Procedure as it stands: [`../../deploy/gpu-backends-aws.md`](../../deploy/gpu-backends-aws.md). The
on-prem half is [`../../deploy/gpu-backends-local.md`](../../deploy/gpu-backends-local.md).

## What the account held when this started

The GPU service quota (`All G and VT instances`, 32 vCPU) was granted after the case recorded in
[`../plans/2026101609_gpu-compute-plane.md`](../plans/2026101609_gpu-compute-plane.md) sat unassigned
for four days. `g6.xlarge` is 4 vCPU, so quota was never the constraint here.

## The run, in order, with what it measured

| step | wall clock | observation |
|---|---|---|
| base AMI as published by Amazon | — | `Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04) 20261016`, root device `/dev/sda1` |
| launch, boot, status checks ok | ~3 min | `nvidia-smi` reports the L4 and driver 595.91; Docker 29.9 is preinstalled; the toolkit needed no work |
| `docker run --gpus all … nvidia-smi -L` | ~2 min | the container sees the card; the toolkit is genuinely present, not merely installed |
| archive → instance → `docker build` | **970 s** | see the breakdown below |
| container start → `/health` 200 | ~95 s | `budget ok: needs 5.5 GB, budget 21.5 GB (22566 MiB free, 1.0 GB held back)` |
| whole-machine `create-image` | **~55 min** | 104 GB snapshot; the instance was stopped for most of it and the snapshot finished anyway |
| prediction through the gateway | 0.24–0.43 s | `gwfss` row 3 → 14 objects, `gwhs-temp` row 0 → 1, `gwhs-temp` row 3 → 2 |

**The build's 970 seconds were not network.** The checkpoint arrived from the weight store in about
a minute (3.5 GB at 40–100 MB/s — a datacenter downlink is not the problem). The time went to `uv
sync` installing 7.5 GB of torch and CUDA wheels, and then to exporting and unpacking sixteen
thousand files into the overlay. That is the number an image registry removes: the pull carries the
finished layers, so neither step runs again.

The image is **24.2 GB** uncompressed: 5.6 GB CUDA base, 7.5 GB virtualenv, 3.5 GB checkpoint, ~0.1 MB
code. The builder cache beside it was another 24.3 GB, and `docker builder prune -af` removed it
before the snapshot — `docker system df` then read `Build Cache 0B`, which is how the answer to "is
the cache in the image?" became a measurement rather than an assurance.

## What broke, and what it cost to learn

- **`--private-ip-address` suppresses the automatic public IPv4.** The subnet maps public addresses
  on launch, but naming a private one produces an instance with no egress at all: no `apt`, no pull.
  The launch succeeded and the machine could not install anything. Fix: an Elastic IP associated
  afterwards, or `--associate-public-ip-address` when the subnet does not already map one.
- **A security-group description cannot contain an apostrophe.** `InvalidParameterValue: Valid
  descriptions are strings … from the following set`, and the apostrophe in "the gateway host's group"
  was the whole failure. The description that shipped avoids possessives.
- **`docker run` blocks while the image is still unpacking, and the daemon looks empty meanwhile.**
  `docker ps -a` printed headers and no rows, `docker logs` said *no such container*, and a second
  `docker run` answered `Conflict … name is already in use by container <id>` — for a container the
  API would not list. It resolves itself when the export finishes. Do not conclude the daemon is
  broken, and do not `rm -f` the name out from under a start that is still in flight.
- **The model API's `(dataset, row)` is a JSON body.** Sending them as query parameters answers `400
  Field required` in 3 ms, which reads like a bad model and is a bad request shape. The container's
  own `/predict` is a different shape again (`input.url`), so a direct call copied from the gateway's
  body 400s too — probe the backend with the token and an empty body, never with a forged payload.
- **`aws login` is a short-lived browser session**, and it is what a fresh v2 offers first. It
  expires, answers `NoCredentials`, and is the wrong credential for work that has to be repeatable
  next month. A long-lived access key in `~/.aws/credentials` is the durable answer, and the docs now
  say so.

## What the numbers say about the design

- A whole-machine AMI of this instance is **104 GB** — much more than the `df` figure of 73 GB,
  because pruned Docker layers still occupy blocks until the filesystem is trimmed. At
  $0.05/GB-month that is **$5.2/month** to keep one machine-shaped snapshot.
- A stopped instance instead of an image would be **$11.4/month** (120 GB gp3) and would pin the
  machine, the type and the AZ.
- A base image of OS, driver and Docker is about **10 GB ($0.5/month)**, and one model in ECR is
  about **10 GB of compressed layers ($1.0/month)**. The pair is ~$1.5/month and keeps the machine
  disposable — the design in [`../../deploy/gpu-backends-aws.md`](../../deploy/gpu-backends-aws.md).

The rehearsal itself cost roughly **$1.6** of instance time, almost all of it the build and the
snapshot. Launches afterwards are minutes, not half-hours.

## What was proven end to end, and how

From the gateway host, over the VPC private address, never from the instance itself: `nc` to `12711`,
`/health` 200 carrying `computed_by.machine = aws-euc1-l4` (`NVIDIA L4`, `8.9`, CUDA 12.8, torch
2.10.0+cu128), the token probe answering `400` with the right token and `401` with a wrong one, and
one real prediction through the gateway whose `computed_by` named the rented machine.

`GET /models` then read `backends_up: 3` for `sam3.1-meta` — the rented one plus two laboratory
machines, with `homepc-4090` having dropped out of that model's list. That is the arithmetic worth
remembering: `backends_up` counts declarations, so a count that does not match the expected machines
means a probe found one *and* lost another, never that a new one failed to appear.

## What this changed in the repository

- `deploy/gpu-backends.md` split into `gpu-backends-local.md` and `gpu-backends-aws.md`: one machine
  you keep and one you throw away have different lifecycles, and a single file was carrying both.
- `rules/backend-pool.md`'s rented row changed from *not created* to an alias with a base image and a
  digest, and its rule from "do not assume that path exists" to "it exists, and no instance runs by
  default".
- `rules/project.md`'s private-network link now points at the file that explains the group-reference
  transport.

## The Ubuntu alternative to the Debian base image

Debian was chosen for the base AMI (smaller cloud image, no desktop stack, no `snapd`). If its DKMS
build turns out to be the problem, the Ubuntu equivalent differs in exactly five places and nothing
after the driver:

| | Debian 13 | Ubuntu 24.04 |
|---|---|---|
| AMI owner | `136693071363` | `099720109477` |
| name filter | `debian-13-amd64-*` | `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*` |
| root device | `/dev/xvda` | `/dev/sda1` |
| SSH user | `admin` | `ubuntu` |
| driver | `apt-get install nvidia-driver` (DKMS, 5–10 min) | `ubuntu-drivers install --gpgpu` (prebuilt `linux-modules-nvidia-*`, no compile) |

The `--gpgpu` flag matters: without it `ubuntu-drivers` may pull the desktop stack (X libraries, ~500
MB) that a headless backend never uses. With Ubuntu there is also nothing to `apt-mark hold` — the
module is a prebuilt package matched to the kernel package, so a kernel upgrade is an ordinary apt
transaction rather than a rebuild at boot.

The image is roughly 2 GB larger than Debian's either way; at $0.05/GB-month that is about ten cents
a month, which is why the choice is about the driver path and not about the bill.

## The layered design, executed the same afternoon

The whole-machine AMI finished (104 GB, ~55 minutes) and was then thrown away. What replaced it, and
what each piece measured:

| Artifact | Size | Idle cost | Notes |
|---|---|---|---|
| Base AMI `base-gpu-debian13-20261027b` | 3.3 GB used, 12.4 GB stored | $0.62/month | Debian 13, NVIDIA 550.163.01 (DKMS), Docker 29.9.2, toolkit, `awscli` 2.23.6 |
| ECR `sam3.1-meta:20261027` | 9.69 GB compressed (24.2 GB unpacked) | $0.97/month | digest `sha256:fad9b3f0…4f9d`, tag immutable |
| Security group, key pair, role, instance profile, SSM parameter, launch template | — | $0 | created once |

The base image was built on a **g5.xlarge** because it was the only GPU the region would give us at
that moment; that choice does not bind the image — the driver is compiled against the kernel, which is
the same cloud kernel on every x86 size, and 550 covers Turing through Hopper. A T4, L4, L40S, A10G or
H100 runs the same image; `g5g` cannot (aarch64), and a Blackwell-generation card would need a rebake.

**Capacity, not quota, was the whole obstacle for 90 minutes.** Every G size — `g6.xlarge`,
`g6.2xlarge`, `g6.4xlarge`, `g5.xlarge`, `g5.4xlarge`, `g6e.*` — failed with
`InsufficientInstanceCapacity` in all three AZs, on demand and spot. The 8xlarge sizes failed
differently: `VcpuLimitExceeded`, because 32 vCPU is the entire G/VT quota. A `g4dn.xlarge` (T4) was the
first thing to launch, so that is what the end-to-end rehearsal ran on.

**The chain ran itself.** The launch template carried the type, subnet, group, fixed address, instance
profile and a user-data script; nothing was typed on the machine. `run-instances` → boot → ECR login →
`docker pull` by digest (about four minutes on a T4's downlink) → token from SSM → container. `/health`
answered `200` from the gateway host with `computed_by.machine = aws-euc1-l4`, `gpu = Tesla T4`,
`compute_capability 7.5`.

**What the same request cost on each card** (`gwhs-temp` row 0, `confidence_threshold` 0.21, one
object either way): **3.5 s on the T4, 0.34 s on the L4**. A T4 proves the path; it does not serve it.

Four things the run corrected, all now in the procedure:

- **The base image needs the AWS CLI.** The first version had no `aws`, so user-data could neither log
  in to ECR nor read SSM. Debian 13's `awscli` package is v2 (2.23.6) — the same as the workstation —
  and one `apt-get install` line settles it.
- **An Elastic IP is unnecessary.** A default subnet assigns a public IPv4 even when
  `--private-ip-address` names the private one; an earlier reading of the run-instances response (which
  reports `None` until the address is assigned) led to an EIP being associated for nothing.
- **A cached answer names the old machine.** Two predictions returned in 5 ms with
  `gpu = NVIDIA L4` while the running instance was a T4: cache hits keep the original `computed_by`.
  Varying one query parameter is what reaches a new backend.
- **The fixed address breaks `known_hosts`.** A relaunch at the same private address arrives with a new
  host key and `ssh` calls it a man-in-the-middle. `ssh-keygen -R <address>` once, and it is a new
  machine rather than an attack.

Teardown after the rehearsal: instance terminated, `models.json` line removed and the gateway
restarted, the 104 GB AMI and its snapshot deleted, the Elastic IP released. What remains is the base
AMI, the ECR digest, the security group, the key pair, the role, the SSM parameter and the launch
template — about $1.6/month, and a launch is one command.

The template keeps **two** versions: v1 `g6.xlarge` is the default because that is the card the third
domain should be, and v8 `g4dn.xlarge` is the one proven to launch while L4 capacity is scarce.

## The second model, pushed from the workstation instead

`gwfss-segformer-b4` was already built and running on the maintainer's workstation (18.2 GB, plus
`sam3.1-meta` at 24.6 GB), so building it again in the cloud would have bought nothing: the image only
had to travel. It was tagged and pushed straight to ECR from Manjaro —
`sha256:cfd6be9f…c98b` — which also settled a claim this file made earlier.

**The campus uplink is not 192 KB/s.** That figure came from an `rsync` to a Frankfurt instance *through
the tailnet jump host*, i.e. it measured Tokyo→host latency and the overlay, not the workstation's own
link. A direct `docker push` to ECR in eu-central-1 measured **3.7–5.7 MB/s (30–45 Mbps)**, which makes
a one-off push of an already-built image the cheaper path whenever the image exists locally, and the
in-cloud build the cheaper path when it does not (there the base image and the wheels arrive at
datacenter speed, and only the layers leave).

The lesson is about measuring the right path: an overlay hop and a direct egress are two different
links, and a number taken on one says nothing about the other.

### The second model, in the same image

`gwfss-segformer-b4` went into ECR the same day (`sha256:cfd6be9f…c98b`, 6.8 GB compressed from an
18.2 GB local image) and the template's user-data now starts **both** containers — `12711` for
`sam3.1-meta`, `12712:12711` for the segformer. The root volume went from 60 GB to **100 GB**, because
two unpacked images are 42 GB before the system.

A launch from that version served both: `/health` 200 on both ports, `computed_by.machine = aws-euc1-l4`
on both, and through the gateway `gwfss` rows 1 and 2 answered in 0.74 s and 0.21 s on the T4.

One routing detail worth keeping: the first `gwfss` request after a gateway restart went to
`homepc-4090`, not the rented machine, even though the rented one was first in `models.json`. The
gateway was twelve seconds old and the new backend had not been probed yet — `models.json` order is a
preference among *known* backends, and a backend becomes known on the probe clock, not on the file.

## What this rehearsal proved, and what it did not

Proved, twice and without a single manual step on the machine: template → boot → ECR pull by digest →
token from SSM → container(s) → `/health` 200 measured **from the gateway host**; the fixed private
address survived three different instances (a DLAMI one and two g4dn); the image built on a `g5`
booted and served on a `g4dn`, which is the portability claim actually exercised rather than argued;
and both models answered through the gateway with `computed_by.machine = aws-euc1-l4`.

Not proved, and worth knowing before December:

- **Capacity is the risk, not the plumbing.** Every G size was unavailable in every AZ for about
  ninety minutes; `g4dn` was the only stock. A third fault domain that cannot be launched is not a
  fault domain. Launch ahead of the window, keep the two template versions, and accept that switching
  AZ changes the fixed address.
- **The `g6.xlarge` (L4) version was never launched from this image.** The image is proven on a T4 and
  the driver is proven on an L4 only from the older DLAMI image. Driver 550 covers both, and the
  kernel is the same, so this is a strong inference — not a measurement.
- **Cold start with two models is 6–9 minutes**, not the three a single model took: two pulls of about
  17 GB compressed, then two checkpoints loading. A launch at 09:55 is a backend by 10:04.
- **A reboot of this image was never tested.** `docker` is enabled and both containers carry
  `--restart unless-stopped`, which is the same chain the DLAMI instance demonstrated; it is still an
  assumption until someone reboots one.
