# The DGX Station V100 answers, and what it already had

2026-11-04. A four-card DGX Station in the plant-factory room, prepared as a possible backend. It is
**reachable and administered**, with no model container yet. Address and alias map:
[`.agents/local/pool.md`](../local/pool.md).

## The machine

| Fact | Value |
|---|---|
| Model | NVIDIA DGX Station (`DGX_SWBUILD_VERSION="5.0.0"`, built 2020-10-26) |
| OS | Ubuntu 20.04.6 LTS |
| Cards | 4 × Tesla V100-DGXS-32GB, 32768 MiB each, PCI 07/08/0E/0F |
| Driver | 550.144.03, CUDA 12.4 |
| CPU / RAM | 40 threads, 188 GB |
| Docker | 24.0.7, `enabled` and `active`, unit carries an `ExecStart` drop-in |
| Toolkit | `nvidia-container-toolkit` 1.17.8, `nvidia-container-runtime` 3.14.0 |
| Account | `fieldphenomics`, in the `docker` group |
| Where | `room-router-e`, the plant-factory LAN |

The desktop is on GPU 0 only: Xorg ×2, `gnome-shell`, RustDesk and `gnome-control-center` hold about
790 MiB there. GPUs 1–3 sat at 11 MiB. So the "card minus 1 GB" reserve is real on card 0 and
pessimistic on the other three; it is applied to the machine as a whole rather than per card.

## How it is reached

Two routes answer, measured rather than assumed:

- **From the workstation** (`crest-nerv`, laboratory LAN): `ip route get` named the default gateway,
  so the campus core routes between rooms — the same route `lab-intel3090` already demonstrated.
  `:22` open; sshd answered `Permission denied (publickey,password)`.
- **From `gwhs-host`**: `ip route get` named `dev tailscale0 table 52`, i.e. the room router's
  subnet route. `:22` open from there too.

Access took one act: the workstation's `~/.ssh/id_ed25519` public key appended to
`~/.ssh/authorized_keys` for `fieldphenomics`, modes 700/600. `ssh -o BatchMode=yes` then returned
`whoami` with no prompt. Nothing else was installed or enabled; `ssh.service` was already running.

The server's banner warns that the session uses no post-quantum key exchange. That is Ubuntu 20.04's
OpenSSH being old, not a fault, and it prints on every connection.

## What Docker already had, and the one surprising reading

`/etc/docker/daemon.json` does not exist. `docker info` reports `Runtimes: io.containerd.runc.v2
runc` and `Default Runtime: runc` — **no `nvidia` runtime at all** — and yet:

```
$ docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu20.04 nvidia-smi -L
GPU 0: Tesla V100-DGXS-32GB (UUID: GPU-81d6cc92-…)
GPU 1: Tesla V100-DGXS-32GB (UUID: GPU-572de892-…)
GPU 2: Tesla V100-DGXS-32GB (UUID: GPU-ddb0f1ce-…)
GPU 3: Tesla V100-DGXS-32GB (UUID: GPU-defed84e-…)
```

`--gpus '"device=2"'` gave a container one card, numbered `0` inside, whose UUID matched host GPU 2.
`/etc/cdi/nvidia.yaml` exists and is the likely mechanism; the point for the next machine is that
**the container test decides, and `docker info` does not** — the doc's "no toolkit" error is a
different failure. The doc now says this in §2.1.

The Docker unit is not stock: a drop-in sets `--default-shm-size=1G --bip=172.17.0.1/16
--fixed-cidr=172.17.0.0/16`. Replacing the service with a bare `dockerd` would drop it.

## Two limits worth knowing before a build

- **Disk.** Docker's root is on `/` (1.8 TB) while a 5.2 TB RAID0 of three disks sits at
  `/ssdraid0` — the roomy filesystem is not the one Docker uses. On 2026-11-04 `/` held 44 GB free
  and 98% used; the cleanup below took it to 930 GB free. Weights and build contexts belong under
  `/ssdraid0`.
- **The card is Volta.** `sm_70` means no bf16 and no FlashAttention-2, and CUDA 13 no longer builds
  for it. Driver 550 / CUDA 12.4 runs any cu12.x image, so the constraint is the model's own wheels,
  not the machine's driver.

Also listening on the LAN already: `ollama` on `0.0.0.0:11434`. It shares the machine but not the
pool's ports (`12711`–`12718`), which were all closed.

## The cleanup of 2026-11-04, and the hazard it left

The instruction was *everything before 2025, plus CVAT, ollama, open-webui and `DPS`*. 138 containers
went: 100 from 2021–2022 (462 GB), 47 from 2023–2024 (172 GB), and the 14 running ones — CVAT's
twelve, `ollama`, `open-webui` — plus `DPS`. `/` went from 44 GB free to 930 GB, 98% to 45%.

Kept on purpose: the four 2025 containers (`ldm` 108 GB, `superres-container` 12.3 GB, `gan` 7 GB,
`wonderful_jepsen`), `ollama-gpu0…3`, every image and every volume.

**Saved first**, under `/ssdraid0/docker-retired-20261104/` (mode 700 — it holds container env
values): `containers.json`, the full `docker inspect --size` of all 138; `manifest.psv`; the `ps`,
`images` and `volumes` snapshots; `ollama-compose.yml`; and `cvat-recovered-compose.yml`.

**The CVAT compose file was already gone.** Its containers still named
`…/synology_nfs/ishii_Pro/cvat/docker-compose.yml` as their source, but that directory holds only
`components/`. The sixteen services were rebuilt from their container configs, so the stack can come
back with `docker compose -f cvat-recovered-compose.yml up -d` on the volumes it already has.

**The hazard: 27 volumes are now referenced by nothing**, seven of them CVAT's — `cvat_cvat_db`
among them, which is where the annotations live. `docker system df` now calls them 100%
reclaimable and `docker volume prune` would delete them without asking. Do not run it, and do not
let it into a cleanup script.

## What ran on it, and how fast

All three on one card, `--gpus device=N`, over a 1024×768 or 1024² frame.

| what | image | VRAM | time |
|---|---|---|---|
| `Qwen3-VL-2B-Instruct`, fp16 | `pytorch/pytorch:2.6.0-cuda12.4` + transformers 5.22 | 4.9 GB (4.5 reserved) | 785 image+text tokens prefill in 0.16 s; **15.8 tok/s** decode; load 3.6 s |
| `WeVisOne-VL-4B`, fp16 | the same image | 10.1 GB (9.7 reserved) | prefill 0.22 s; **11.2 tok/s** decode; load 2.5 s of the 248 s first run, the rest download |
| `gwfss-segformer-b4`, the pool's own container | built here from the submodule | 1.17 GB | **0.21–0.49 s** per 1024² row, 3 foreground classes, `computed_by` reporting V100 / 7.0 |

Three facts worth keeping:

- **torch 2.10.0+cu128 still carries Volta.** `get_arch_list()` on this machine answers `sm_70` …
  `sm_120`, so the pool's own images run unmodified — no rebuild for a lower CUDA.
- **float16, never bfloat16.** V100 has no bf16 hardware; both VLMs were cast to fp16 at load and
  answered without NaNs. Anything that insists on bf16 has to be changed, not configured.
- **WeVisOne answers in its own format without its package**: asked to count circles it returned
  `<ref>circles</ref><point><147><259>,<293><676>,…`, which is the coordinate-token contract working.
  Turning those into masks needs its 9.6M-parameter decoder from the GitHub package, which was not
  installed at the time. It has since been measured, and it does produce masks — 26 s and 15 GB for
  43 wheat heads — once one fp16 buffer cast is fixed:
  [`2026110521_wevisone-vl-masks.md`](2026110521_wevisone-vl-masks.md).

At 11–16 tok/s, 60 tokens of prose cost 4–5 s and 200 cost 15: a VLM in this pool would need a
declared `response_timeout_seconds`, and it is not a chat interface.

**The dtype is now chosen by the card and reported.** `dinov3` and `internvlu` pick `bfloat16` on
compute capability 8.0 and above and `float16` below it (`DINOV3_DTYPE` / `INTERNVLU_DTYPE` override
it), and every container puts the resulting name in `computed_by.dtype`. The other five report what
they always ran: `float32` for `gwfss-segformer-b4`, `tasselnetv4`, the YOLO pair and
`sprout-l-gwfss`, and `bfloat16` for `sam3.1-meta`, where it is the model's own autocast rather
than a deployment choice — which is why that one cannot run here at all.

## Not done, and the decision in front of it

No model container, no published port, no entry in `models.json`, no row in
[`backend-pool.md`](../rules/backend-pool.md) — it is not a backend yet, so the inventory is still
accurate.

The decision is how four cards fit a pool built around one card per machine. The router sums queue
depth per `computed_by.machine`, and one host shares one address, so four containers of one model
need four machine names and four host ports. The port table in
[`deploy/gpu-backends-local.md`](../../deploy/gpu-backends-local.md) §1 names one number per model;
the multi-card case is now stated there as the departure, and the run line in §3.2 carries the
`--gpus '"device=N"'` form.

## What this cost, and what the doc now says instead

- The doc's Linux line was `systemctl enable --now sshd`. On Debian/Ubuntu and DGX OS the unit is
  `ssh`; `enable sshd` answers `Unit sshd.service does not exist`.
- "Every administrative connection goes through the gateway host" was false for a campus-routed
  LAN, which is where this machine sits. §1 now separates the two paths and gives a direct `Host`
  block.
- The key step was missing entirely: the existing machines already had keys, so nothing described
  adding one. §1 now shows it.
- `--gpus all` on a four-card host hands one container all four. §3.2 now says `device=<index>`, one
  machine name per card, one port per card.
