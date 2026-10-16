# The compute plane's backends

Which machines can serve a model, what each can carry, how one is brought up, and where the gateway's
list of them lives. This file is a **document**: no code reads it, so a stale row costs a wrong
judgement and never a wrong route. What the gateway actually reads is host configuration —
[below](#the-list-the-gateway-reads).

Why a backend needs no tailscale client, and the measurements this table rests on, are in
[`.agents/references/2026101610_backend-reachability-over-the-room-routers.md`](../.agents/references/2026101610_backend-reachability-over-the-room-routers.md).
The contract a backend container implements is in
[`.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md).

## How a backend is addressed

Two things make a machine reachable, and neither is a client on the machine:

1. It sits on one of the LANs below. That LAN's **router is the tailnet node**, advertising its `/24`,
   and the gateway host accepts routes — so a private address inside the `/24` is reachable from
   Frankfurt with nothing installed on the machine.
2. **A container publishes its port on the LAN interface**, which is `docker run -p 8000:8000`: the
   default binding is `0.0.0.0`, and that is what the router forwards into.

Binding the container to `127.0.0.1` and publishing it with `tailscale serve` is an *alternative* that
needs a tailscale client on that machine, and it is not what this pool does. Binding to `127.0.0.1`
alone leaves nothing for the router to reach. On Docker Desktop the port is published by a proxy
running on **Windows**, which is exactly the interface the router forwards to; inside WSL2 without
Docker Desktop it is not, and LAN access would need `netsh portproxy` or mirrored networking — the
reason the Windows machine below uses Desktop.

Convention: the inference port is **8000**, which is what the plan's examples assume.

## The inventory

| Machine | Where | Advertised by | Private address | GPU | VRAM | Fault domain | Last measured |
|---|---|---|---|---|---|---|---|
| `labpc` (3090) | laboratory | `room-router-a` (GL-BE9300), `the laboratory LAN` | `lab-3090` | RTX 3090 | 24576 MiB | laboratory | 2026-10-16 — TCP 2233/8771/22000 open, ICMP 337–350 ms, 0% loss |
| `homepc` (4090) | home | `home-router` (GL-MT6000), `the home LAN` | `homepc-4090` | RTX 4090 | 24576 MiB | home | 2026-10-16 — TCP 22/22000 open, ICMP 242–250 ms, 0% loss |
| lab 5090 (Windows 11) | laboratory | `room-router-d` (GL-MT6000), `the 5090's LAN` | `lab-5090` | RTX 5090 | **32607 MiB** | laboratory | 2026-10-16 — **TCP 8000 open and answering HTTP from the host**, ICMP blocked by Windows |
| a second 5090 | borrowed from a company | ? | ? | RTX 5090 | 32 GB | likely laboratory | not delivered |
| ~3 more 90-class | laboratory | ? | ? | unknown | ~24 GB | laboratory | not measured |
| a rented instance | `eu-central-1` | its security group — same VPC as the gateway, so no tailnet at all | assigned on launch | L4, or L40S | 24 GB, or 48 GB | AWS | not created |
| ETH hosts | Zurich | not negotiated | not negotiated | unknown | unknown | ETH | not negotiated |

Empty cells are **unknown, not absent** — fill them in when the machine is measured, and keep the row
when it stops existing rather than deleting it quietly.

**The 5090 is the largest card in the pool** (32607 MiB against 24576 MiB) and the only one on Windows,
so it is both the best candidate for concurrency and the most expensive to bring up.

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

    sam3.1:  ["http://<address>:8000"]
    yolo:    ["http://<address>:8000", "http://<address>:8000"]

The gateway tries a model's backends in order and marks a failed one unhealthy for 30 seconds. A
model's list is the **only** place its backends appear: each backend declares at startup which models
it serves and how many it can hold in flight, so capability is derived from that declaration and the
list rather than written down a second time here.

## Bringing a machine up

Both recipes end in the same three facts: Docker runs, the GPU is visible **inside a container**, and
the port answers **from the host**. Prove them in that order; a failure in the third is a network
question and a failure in the second has nothing to do with the network.

### Manjaro (the laboratory 3090 and the home 4090 — one recipe for both)

```bash
# 1. driver, then docker, then the toolkit that lets a container see the GPU
sudo mhwd -a pci nonfree 0300            # Manjaro's own way to install the NVIDIA driver
sudo pacman -S docker nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl enable --now docker       # a real boot service: no login required
sudo usermod -aG docker "$USER"          # then log out and back in once
```

```bash
# 2. the GPU must be visible inside a container, not merely on the host
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

The package names are the ones to check first if something is missing — `nvidia-container-toolkit` may
come from the AUR on some Manjaro branches. **Do not copy the Windows machine's driver notes here:**
on WSL the driver is supplied by Windows, and on Manjaro it must be installed and the toolkit
configured, or `--gpus all` silently finds nothing.

### Windows 11 (the laboratory 5090)

Docker Desktop, not `apt install docker.io` and not snap: inside WSL2 the package-manager dockers
publish their ports *inside the WSL VM*, where the router cannot reach them.

1. **Update the NVIDIA Windows driver** if it predates the 570 branch — a 50-series card needs it, and
   an old driver leaves `nvidia-smi` unable to see the card *inside WSL*.
2. Install Docker Desktop (the Microsoft Store build works — it has no "WSL 2 based engine" checkbox any
   more, because WSL2 is now the only engine).
3. **Settings → Resources → WSL Integration** → enable for the distro. Without it the `docker` command
   does not exist inside WSL, and the error looks like a missing Ubuntu package (`Command 'docker' not
   found, but can be installed with: sudo snap install docker`) — which it is not.
4. **Never install a Linux NVIDIA driver inside WSL.** The driver comes from Windows through
   `/usr/lib/wsl/lib`.
5. Verify in WSL: `nvidia-smi`, then the `docker run --rm --gpus all … nvidia-smi` line above.
6. **A manual firewall rule is not needed.** Docker Desktop adds one when it publishes a port; if a port
   ever does not answer, the fallback is:

   ```powershell
   New-NetFirewallRule -DisplayName "biohack backend 8000" -Direction Inbound -Protocol TCP `
     -LocalPort 8000 -RemoteAddress <the 5090's LAN>/24,100.64.0.0/10 -Action Allow
   ```

   Both address ranges are in there because the router may forward the original tailnet source
   (`100.64.0.0/10`) or NAT it to the local subnet.

7. **Windows blocks ICMP echo by default**, so a machine that answers nothing to `ping` is not
   necessarily down. Never use ping as the reachability test for a Windows backend — use a TCP probe.
8. The `docker` command works in WSL and, in a *newly opened* PowerShell, in Windows too. One daemon
   serves both, and which shell created a container has no bearing on its restart policy.

### Surviving a reboot, unattended

This is the part that fails during an event and looks like a network fault.

- **`docker pull` is a one-time act; it is not a boot-time one.** `--restart unless-stopped` restarts a
  container that exists — it never re-pulls. So: pull once, `docker run -d --restart unless-stopped`
  once, and the policy carries it from then on. Moving to a new image is a deliberate act (`docker pull`
  the new digest, recreate the container), which is what pinning a digest means.
- **`docker login ghcr.io` once** with a read-only token (`read:packages`). The credential persists.
- On **Manjaro** the chain is one link: `systemctl enable docker`, then the restart policy.
- On **Windows the chain has a weak link**: Docker Desktop is a user-level program, so with nobody
  logged in after a reboot the engine never starts and no policy can help. Settings → General → *Start
  Docker Desktop when you sign in* must be paired with **Windows automatic sign-in**, plus sleep and
  hibernation off and Windows Update reboots deferred for the event window.
- Verify by actually rebooting the machine and watching the container come back. It is the only test
  that finds a broken auto-sign-in, and it is cheap now.

## Adding a machine

1. Put it on one of the LANs above, and follow the recipe for its OS.
2. Read its declaration and check the machine can take it — `docker image inspect` for the label,
   `nvidia-smi --query-gpu=index,memory.free --format=csv` for the cards.
3. Start it detached and publishing on the LAN: `docker run -d --restart unless-stopped -p 8000:8000 …`
4. **Prove it from the host, not from the machine**: `nc -vz <private address> 8000`.
5. Add that address to the model's list on the host, and add this machine's row above.

## How to re-measure a row

```bash
# who advertises which /24 — the answer is in AllowedIPs, and only there
ssh -i ~/.ssh/gwhs-demo.pem <user>@gwhs-host \
  'tailscale status --json' | python3 -c 'import json,sys; [print(a, v["HostName"], v["TailscaleIPs"][0]) for v in json.load(sys.stdin)["Peer"].values() for a in v["AllowedIPs"] if a.startswith("192.168")]'

# reachability, run on the host: the subnet routes live in their own table, so `ip route` shows none
ssh -i ~/.ssh/gwhs-demo.pem <user>@gwhs-host \
  'ip route get lab-3090; nc -vz lab-3090 8000'
```

Two traps this already cost us once each: a **refused port** is not an unreachable host (the laboratory
3090 refused 22 only because it runs no `sshd`), and **a missing ping reply** is not one either (the
Windows machine blocks ICMP while answering HTTP). Ask for a TCP connection, and ask the machine what
it listens on (`ss -lnt`) before concluding anything about the path.
