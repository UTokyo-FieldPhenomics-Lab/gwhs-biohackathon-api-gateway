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
2. **A container publishes its port on the LAN interface**, which is `docker run -p 12711:12711`: the
   default binding is `0.0.0.0`, and that is what the router forwards into.

Binding the container to `127.0.0.1` and publishing it with `tailscale serve` is an *alternative* that
needs a tailscale client on that machine, and it is not what this pool does. Binding to `127.0.0.1`
alone leaves nothing for the router to reach. On Docker Desktop the port is published by a proxy
running on **Windows**, which is exactly the interface the router forwards to; inside WSL2 without
Docker Desktop it is not, and LAN access would need `netsh portproxy` or mirrored networking — the
reason the Windows machine below uses Desktop.

Convention: the inference port is **12711**, inside the container and on the host alike — one number,
never a mapping. It is not a service port, and it sits outside both ephemeral ranges that could take it
from under us (Linux `32768–60999`, Windows `49152–65535`). It is also deliberately **not 8000**: the
gateway's own socket is `127.0.0.1:8000` on its host, and a document where one number means two things
is how a backend ends up answering on the gateway's port.

Convention: **a container is admitted against the card minus 1 GB, never against the card.** These are
workstations whose desktop drives the same display, so a 24 GB card is placed against **23 GB** and the
32 GB card against **31 GB**. The limit is imposed rather than trusted to be respected, and it is
written here as well as in the deploy script because a manual `docker run` goes around the script while
the person sitting at the machine still has to use their desktop. For scale: the demo model holds
**8.5 GB resident**, so every card in the inventory below has room to spare.

## The inventory

| Machine | Where | Advertised by | Private address | GPU | VRAM | Fault domain | Last measured |
|---|---|---|---|---|---|---|---|
| `labpc` (3090) — host `labpc-host`, tailnet `labpc-tailnet` | laboratory | `room-router-a` (GL-BE9300), `the laboratory LAN` | `lab-3090` | RTX 3090 | 24576 MiB | laboratory | 2026-10-17 — serves from the container (`budget ok: needs 5.5 GB, budget 23.0 GB`, reloading in ~8 s); **`sshd` refuses port 22**, so nothing is administered over SSH. The toolkit that had to be installed, the measurements, and two corrections this row used to carry: [`2026101709`](../.agents/references/2026101709_the-3090-becomes-a-container.md) |
| `homepc` (4090) | home | `home-router` (GL-MT6000), `the home LAN` | `homepc-4090` | RTX 4090 | 24576 MiB | home | 2026-10-16 — TCP 22/22000 open, ICMP 242–250 ms, 0% loss |
| lab 5090 (Windows 11) | laboratory | `room-router-d` (GL-MT6000), `the 5090's LAN` | `lab-5090` | RTX 5090 | **32607 MiB** | laboratory | 2026-10-16 — SSH, Docker and **GPU inside a container** all verified; driver 610.88; ICMP blocked by Windows. Then **built and serving** the same day: `sam3.1-meta` built on the machine from a 2.1 MB archive, `/health` 200 **from the gateway host**, declared ready with load 10.8 s and warm 1.2 s, and a `predict` through the gateway answered 200 in 0.9 s (`gwhs-temp` row 0: one `wheat head`, score 0.52, 173716 px). It held **~6.5 GB resident** where the label declares 3.7 — see the note below the table |
| a second 5090 | borrowed from a company | ? | ? | RTX 5090 | 32 GB | likely laboratory | not delivered |
| ~3 more 90-class | laboratory | ? | ? | unknown | ~24 GB | laboratory | not measured |
| a rented instance | `eu-central-1` | its security group — same VPC as the gateway, so no tailnet at all | assigned on launch | L4, or L40S | 24 GB, or 48 GB | AWS | not created |
| ETH hosts | Zurich | not negotiated | not negotiated | unknown | unknown | ETH | not negotiated |

Empty cells are **unknown, not absent** — fill them in when the machine is measured, and keep the row
when it stops existing rather than deleting it quietly.

**The 5090 is the largest card in the pool** (32607 MiB against 24576 MiB) and the only one on Windows.
It is **not** the best candidate for concurrency, which is what an earlier version of this line assumed:
concurrency buys nothing on these models — one request saturates a card, measured — so what it adds today
is a **second fault domain**. Whether it is also a faster serial lane is **not established**: the two
cards have never been measured on the same frame, and the one figure that looks comparable — the warm
prompt at start-up — came out at 0.34 s on the 3090 and 0.91–1.21 s on the 5090 across three runs, which
is startup noise and lazy kernel compilation rather than a ranking.

Bringing it up is no longer expensive: one 2.1 MB archive plus a build that fetched its own 3.5 GB
checkpoint on the machine's own downlink (3 m 33 s), against the alternative of moving a 14 GB image over
one sender's uplink. What it cost instead was the credential-helper obstacle below — which applies to a
**build**, not only to a pull.

**The resident figure is the 3090's and understates this card.** The container declares
`resident_gb 3.7` computed from a 3812 MiB measurement on the 3090; on the 5090 the same container left
32607 − 24924 = ~7.7 GB used, of which ~1.1 GB was the desktop, so roughly **6.5 GB resident** and a
larger CUDA context. Admission is unaffected here (31 GB budget against 5.5 GB requested), and a
container's declaration is what the deploy script and a human read before starting it — so the number is
worth re-measuring on this card before it becomes the machine the others are placed against.

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
pool in `~/.config/biohack-gateway/` — the same reasoning as §1.5 of the runbook. The file is
`models.json`, the same shape and the same place as `tokens.json`, one entry per model:

    {"sam3.1-meta": ["http://<address>:12711"],
     "yolo":       ["http://<address>:12711", "http://<address>:12711"]}

A file that does not parse means **no models**, never all of them: the surface fails closed the way
the request gate does, so a typo in host configuration cannot become a gateway that answers for
models nobody configured.

**Its twin is the token, and the token is not decoration.** The gateway presents `X-Backend-Token` on
every call to a backend, which checks it against the value it was started with (`BACKEND_TOKEN` in its
container environment); the gateway's copy is `BIOHACK_BACKEND_TOKEN` in the host's `env`
([`README.md`](README.md)). It is the **only** lock on that hop, and it is what keeps a device on the
shared tailnet from taking the card the event is sharing, one request at a time — a denial of the
event's scarcest resource rather than a leak. What it does **not** cover is an SSRF from a backend, and
it does not have to: the container fetches only under the configured `data.*` suffix.

**A backend whose token does not match does not fall through to the next one — it takes the model down
with it.** The gateway reads `401`/`403` from a backend as *its own* credential being refused, which
cannot be the caller's fault: it logs `a backend refused the gateway's token`, answers `503`, and never
tries the model's other backends. So a list whose second entry was started with a different value is a
model that serves fine until its first entry fails and then fails entirely — measured on 2026-10-16,
when the 5090 was listed first with a token the 3090 had not been started with. Two ends, one value:
rotating it means editing `env` **and** restarting every container on the list, and a newly added
machine is not a backend until that is done.

**And transcribe the value from `env`, never from a message.** One character is a different token, and
`/health` cannot show the difference: it needs no token, so a mismatched end still declares itself and
`GET /models` still reads `backends_up: 2` while every request that actually reaches the odd end
fails. Measured 2026-10-16: the host and the 5090 held the string *with* one leading underscore (43
characters) and the 3090 held it *without* (42). The executed `docker run` in the session that started
the pool used the 42; the 43 was in a message that quoted the value rather than copying it, and a
leading underscore in prose is exactly the character a renderer or a reader drops. The authority is
the file, and the check is one request — with the candidate in a shell variable on the host:

    curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<backend>:12711/models/<model>/predict \
      -H "X-Backend-Token: $T" -H 'Content-Type: application/json' -d '{}'

`401` carrying `bad or missing X-Backend-Token` is the value being wrong. **Any other 4xx means the
token passed** and the deliberately empty body is what was refused — which is why the probe sends a
body it knows is invalid rather than a real one: it never runs an inference.

**One value, every end.** Rotating it is not one container restart: it is one `EnvironmentFile=` and a
restart of every backend on every model's list, and a machine started with the old value is not merely
stale, it is the failure above. There is no second copy anywhere and the value never enters the
repository.

**Why not an address allowlist instead: what a backend sees of its caller is a fact about the path.**

| Path | What arrived | Measured |
|---|---|---|
| `labpc`, behind `room-router-a` | the caller's own tailnet address (in `sshd` on port 22) | 2026-10-16 14:12 and 15:01 JST |
| the 5090, behind `room-router-d` | `the 5090's own proxy` — **not** the caller, and which hop put it there is not identified | 2026-10-16, from the firewall rule it needed |

So an allowlist would hold on one machine and not on another, and it would bind a backend to the
*gateway's* address — the one value this design wants to be disposable, since the tailnet address holds
only until that node is re-registered while replacing the host is an ordinary event. A credential
carried in the request is the same on every path and survives a rebuilt gateway, which is why it is
the lock. Address-based scoping is the option considered and not taken; it becomes worth revisiting if
a backend ever lives where the caller's address both survives and is stable, which neither path is
today.

**To measure a path, ask the container and not the machine.** Start the container, then from the
gateway run `curl -s http://<backend-lan-address>:12711/health` and read the client address in the
container's own log (`INFO: <address>:<port> - "GET /health HTTP/1.1" 200 OK`). On Linux that agrees
with `sshd`; on Windows it does not, because Docker Desktop's port proxy is a hop of its own — the hop
no router setting can remove.

The gateway tries a model's backends in order, preferring one with nothing in flight, marks a failed
one unhealthy for a window that outlives the failure it remembers, and moves on. A model's list is the
**only** place its backends appear. What the model *is* — its resolution, its control parameters, how
many requests it can hold in flight — is declared by the backend itself at its `/health`, which the
gateway reads once at startup and repeats in `GET /models`. Capability is therefore derived from the
machine rather than written down here a second time.

**One budget per attempt, and a window that outlives it — measured, wrong, and then fixed.** With the
list as `[5090, labpc]` and the 5090's container stopped, a request first answered in **61.2 s**: the
gateway waited a whole request timeout (then 60) before trying the second backend, labpc then answered
the same request correctly in the remainder, and the journal said `backend http://<address>:12711
failed a sam3.1-meta: unreachable`. The failed backend was then passed over for 30 s — **shorter than
the failure it remembered** — so the request arriving after that window paid the same minute again, and
every request arriving during it queued behind.

Fixed the same day in [`../gateway/src/biohack_gateway/models.py`](../gateway/src/biohack_gateway/models.py),
as a relationship rather than a number: one response budget per attempt (`RESPONSE_TIMEOUT_SECONDS`,
10 s — an order of magnitude over the 0.3 s a backend needs for an 18-megapixel frame), a window that
`models.py` asserts is at least that long, the connect phase bounded on its own (3 s), and routing that
prefers a backend with nothing in flight so a burst spreads rather than queues. `gateway.md` carries
the invariant; the constants carry the measurements.

Re-measured with the same four-request burst and the 5090 stopped: **4.25 s for the one request that
meets the dead backend**, and 1.4–1.7 s for the other three, with **exactly one** attempt against the
dead backend in the journal — the other three never touched it. The 4.25 s decomposes as the 3-second
connect bound plus labpc's 1.34 s, which is also the correction to what this file said earlier: a
stopped container on that Windows host does **not** accept the connection, so the *connect* bound is
what its death costs, and the response budget is a ceiling for a backend that accepts and then goes
quiet. A machine that is switched off drops the SYN and costs the same 3 s.

**A backend that was loading when the gateway started is not lost.** The gateway asks an undeclared
backend again — every 3 seconds while it answers "still loading", every 30 while it does not answer at
all — so the ordinary boot race where the gateway comes up first resolves itself without anyone
restarting the gateway. A backend that has declared something is never re-read until the gateway
restarts, which is what keeps this from being a health loop.

## Bringing a machine up

Both recipes end in the same three facts: Docker runs, the GPU is visible **inside a container**, and
the port answers **from the host**. Prove them in that order; a failure in the third is a network
question and a failure in the second has nothing to do with the network — and on Linux the second one
is a missing toolkit far more often than a missing driver.

### Linux, both families (Manjaro/Arch and Debian/Ubuntu)

Only the package line differs between distributions. Everything else — the toolkit, the runtime it
registers with Docker, the boot service, the group — is the same, and all of it needs `sudo`.

```bash
# Arch family (the laboratory 3090, the home 4090)
sudo mhwd -a pci nonfree 0300                       # Manjaro's own driver install; plain Arch: nvidia-dkms
sudo pacman -S --needed docker nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl enable --now docker                  # a real boot service: no login required
sudo usermod -aG docker "$USER"                     # then log out and back in once
```

```bash
# Debian family — NVIDIA's own archive, because the toolkit is not in the distribution's
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**Without the toolkit, `--gpus all` fails rather than falling back to the host's driver**, and the
message names the cause if you know what to read:

```
$ docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi -L
docker: Error response from daemon: failed to discover GPU vendor from CDI: no known GPU vendor found
```

That is *not* a driver problem — `nvidia-smi` on the host was fine, and the same container without
`--gpus all` started normally. It is the toolkit alone that is missing. Measured on the laboratory 3090
on 2026-10-16, where the pool's second backend therefore ran as a bare process for hours instead of a
container, with none of the reboot survival this document assumes.

Two things that bite after the install:

- **`nvidia-ctk runtime configure` rewrites `/etc/docker/daemon.json`.** On a machine whose Docker
  storage is not the default — `{"data-root": "/home/…"}`, which is how a small root partition is
  survived — check that key is still there afterwards. If the merge drops it, Docker starts using
  `/var/lib/docker` and every image *looks* as if it vanished, while it sits untouched in the old
  directory. On the 3090 the merge kept it and added the runtime; that is luck to verify, not trust.
- **`systemctl restart docker` stops whatever was running.** Every backend here is started with
  `--restart unless-stopped` and comes back on its own, so the cost is one lost request per machine —
  but it is a real cost, not a no-op.

Then prove the two facts that matter, on every distribution:

```bash
nvidia-smi                                                     # the host sees the card
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi -L   # a container sees it
```

`nvidia-container-toolkit` may come from the AUR on some Manjaro branches — if the package line fails,
that is the first thing to check. **Do not copy the Windows machine's driver notes here:** on WSL the
driver comes from Windows, and on Linux it must be installed and the toolkit configured, or `--gpus all`
finds nothing.

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
   New-NetFirewallRule -DisplayName "biohack backend 12711" -Direction Inbound -Protocol TCP `
     -LocalPort 12711 -RemoteAddress <the 5090's LAN>/24,100.64.0.0/10 -Action Allow
   ```

   Both address ranges are in there because the router may forward the original tailnet source
   (`100.64.0.0/10`) or rewrite it to the local subnet. It turned out to be the second: `sshd` logs the
   source as `the 5090's own proxy`, which is **not** the caller. Which hop rewrote it is not identified and
   does not have to be — what matters is that **a rule here cannot tell one tailnet peer from another**,
   the same conclusion as leaving the tailnet ACL alone. The container then sees a third address again,
   Docker Desktop's proxy being a hop of its own; "Its twin is the token" below records what that means
   for the pool.

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
- **On Windows, the credential helper needs a logon session an SSH session does not have.** A pull
  from an SSH connection fails before it reaches any registry: `error getting credentials - err: exit
  status 1, out: A specified logon session does not exist. It may already have been terminated.`
  Docker Desktop's helper cannot start there, so this hits *anonymous* pulls too — the 5090 could not
  pull `hello-world` over SSH while its engine was otherwise healthy. **It hits `docker build` as well:**
  building the model container died on `COPY --from=ghcr.io/astral-sh/uv:0.12` with that message, while
  `docker manifest inspect` against the same registry, from the same session a minute earlier, had
  succeeded — so that check is not the green light it reads as.
  Three ways out, in the order worth trying: **point the CLI at an empty config directory**
  (`DOCKER_CONFIG=/tmp/dcfg docker build …`), which skips the credential lookup without touching the
  machine's Docker Desktop settings and is safe because every image a model container pulls is public;
  **turn the helper off for that machine**; or **build on the workstation and `docker save | docker
  load`** across the link, which needs no registry and no internet on the target and moves the whole
  14 GB image over one sender's uplink.
- On **Manjaro** the chain is one link: `systemctl enable docker`, then the restart policy.
- On **Windows the chain has a weak link**: Docker Desktop is a user-level program, so with nobody
  logged in after a reboot the engine never starts and no policy can help. Settings → General → *Start
  Docker Desktop when you sign in* must be paired with **Windows automatic sign-in**, plus sleep and
  hibernation off and Windows Update reboots deferred for the event window.
- Verify by actually rebooting the machine and watching the container come back. It is the only test
  that finds a broken auto-sign-in, and it is cheap now.

## Controlling a container on a machine you are sitting at

Everything below is `docker`, and it is the same on both systems: one container per model, named after
the model id it serves. What differs is how you get a shell and what starts the engine after a reboot.

**The parameters live in the container, not in the image**, and that is the whole reason this section
ends in a complete run line. `docker run` puts each flag into the container's own configuration:

| flag | stored as | read it back with |
|---|---|---|
| `--name <model>` | the container's name | `docker ps -a` |
| `--gpus all` | `HostConfig.DeviceRequests` | `docker inspect --format '{{.HostConfig.DeviceRequests}}' <model>` |
| `--restart unless-stopped` | `HostConfig.RestartPolicy` | `docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' <model>` |
| `-p 12711:12711` | `HostConfig.PortBindings` | `docker port <model>` |
| `-e BACKEND_TOKEN=…` | `Config.Env` | `docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' <model>` |

So `docker start` needs no flags: everything is already recorded, which is why stopping and starting is
safe and why a *changed* value means creating the container again. **Only the restart policy can be
changed in place** (`docker update --restart unless-stopped <model>`, not exercised here); an
environment variable, a port, the GPU access or the name cannot, and each needs `docker rm -f` and the
run line below.

### The commands you will actually use

    docker ps                      # what is running
    docker ps -a                   # including anything you stopped
    docker stop <model>            # stop it, and it stays stopped
    docker start <model>           # start it again, with everything it was created with
    docker restart <model>         # stop and start, which is not the same as re-creating it
    docker logs --tail 40 <model>  # what it printed, including why it refused to start

**Creating it, with every flag, because none of it is recoverable later:**

    docker run -d --gpus all --restart unless-stopped -p 12711:12711 \
      -e BACKEND_TOKEN=<the value in the host's env> --name <model> <image>

- `-e BACKEND_TOKEN=…` is **mandatory** — the entrypoint refuses to start without it — and it must equal
  the host's `BIOHACK_BACKEND_TOKEN`. A different value is not a stale backend, it is a dead model: it
  answers `401`, and the gateway abandons the whole model rather than trying the next machine.
- `--gpus all` is what puts the card inside the container. Without it the container finds no GPU and
  refuses to start, which is the entrypoint's admission check doing its job.
- `--restart unless-stopped` is what brings the backend back after a reboot. Never `always`: the
  entrypoint refuses to start when the card cannot take it, and `always` turns that refusal into a loop.
- `-p 12711:12711` publishes on the LAN. Do not bind loopback, and do not publish a different host port
  than the container's: the gateway's list, the router and the Windows firewall rule all name one number.

**Started is not ready.** `/health` answers `503` while the checkpoint loads and one throwaway prompt
runs; `docker logs` prints `budget ok: needs … GB` and then `ready: load … s`, and only then does it
serve. The gateway treats a `503` as "still loading" and asks again, so a restart costs the pool
seconds, not a restart of anything else.

**Prove it from the gateway host, not from the machine.** `curl http://<private address>:12711/health`
is the path a participant's request takes. From the machine itself, `curl http://127.0.0.1:12711/health`
proves only that the container listens — on Windows that is a different interface from the one the router
forwards to.

**A backend left stopped while it is still in a model's list costs the pool one wait.** Measured: the
first request that meets it takes ~4.25 s and is then answered by the other machine, and the request
after each 30-second window pays it again — the numbers are in the routing section above. If a machine
is going to be down for a while, remove its address from `models.json` on the host and restart the
gateway; that is one line and no second copy of the truth.

### Windows 11: the GUI controls, the CLI creates

- **Docker Desktop's Containers pane runs the lifecycle.** The row for `<model>` has Start, Stop,
  Restart and Delete, and its Logs tab is the same text as `docker logs`. It is the same engine as the
  CLI, so a container started in the GUI and one started from a shell are indistinguishable.
- **Create from the CLI anyway.** The run dialog is not where to go hunting for the GPU flag and the
  restart policy, and one line with every flag is unambiguous. (The dialog's fields are not verified
  here; the CLI path is what this project used.)
- Reaching a shell: SSH, PowerShell, or WSL — `docker` is on the PATH in all three, which is one of the
  few things on this machine that *is* straightforward. This project drove it as
  `ssh lab-5090 'wsl -e docker ps'`. In an SSH session the default shell is `cmd`, so commands are
  separated with `&`, not `;`, and a quoted argument has to survive that shell on its way to WSL.
- Coming back after a reboot is Docker Desktop's problem, not the container's: see
  [Surviving a reboot, unattended](#surviving-a-reboot-unattended) immediately below.

### Linux: systemd, the group, and no GUI

- **The engine is a systemd service**: `systemctl status docker`, and `sudo systemctl enable --now
  docker` if it is not up. A machine with no GUI has no other interface than this section.
- Either prefix the commands with `sudo`, or join the group once and log back in — membership is read at
  login, so the second command looks like a typo until you do:

      sudo usermod -aG docker "$USER"

- One container, one card, one port, exactly as on Windows: the same `docker start|stop|restart`, the
  same run line, the same `/health` check from the gateway host.
- If the container starts and then refuses, check the card is visible to the engine at all: `nvidia-smi`
  on the host, and `nvidia-container-toolkit` configured, or `--gpus all` silently finds nothing. The
  per-OS bring-up recipe above is where that is set up.

## Reaching a machine to look at it

**Only the gateway host can reach a backend's address.** It is the one machine that accepts the
tailnet's subnet routes, so every administrative connection goes through it as a jump host. A
workstation on the laboratory LAN routes `lab-5090` to its own default gateway and gets nowhere —
a backend is unreachable "from the lab" even while it is sitting in it.

    Host gwhs-host
      HostName gwhs-host
      User admin
      IdentityFile ~/.ssh/gwhs-demo.pem

    Host lab-5090
      HostName lab-5090
      User "Field of Phenomics"
      IdentityFile ~/.ssh/lab-5090
      ProxyJump gwhs-host

**The account name is not the profile directory name, and on Windows they usually differ.** The
5090's key sits in `C:\Users\45952`, but the account is `Field of Phenomics`; sshd resolves the
account name, so sending the directory name answers `Invalid user 45952` **before it looks at any
key** — which reads exactly like a rejected key and cost an hour here. Ask the machine with `whoami`,
whose output is `machine\account`. A name with spaces needs double quotes in `ssh_config`; both
`User "Field of Phenomics"` and `-l "Field of Phenomics"` work.

On **Manjaro**, `sshd` is normally installed and only needs enabling: `systemctl enable --now sshd`.
**Windows ships no SSH server at all**, so it needs one before any of the above means anything.

### Windows 11: an administrative shell

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server" -Enabled True -Direction Inbound `
  -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress <the 5090's LAN>/24,100.64.0.0/10
```

`-RemoteAddress` is the **source**. `100.64.0.0/10` is the tailnet itself and is what lets the gateway
host connect; the local `/24` covers the case where the router NATs the source rather than preserving
it. **It NATs.** sshd logs every arrival as coming from `the 5090's own proxy`, the router, so the tailnet
range in that rule matches nothing and a rule on this machine cannot distinguish one tailnet peer from
another regardless. **Never widen it to `Any`** — that is the public internet, and it is the whole
difference between "the tailnet can reach it" and "anyone can".

`docker` **is** on the PATH of an SSH session — the CLI needs no full path after all, and `docker
version` answers with the Desktop engine.

Then the key — and the three ways **writing it** fails, all of which present as the same symptom:

```powershell
$key = 'ssh-ed25519 AAAA… lab-5090 backend'
$p = "$env:ProgramData\ssh\administrators_authorized_keys"      # NOT %USERPROFILE%\.ssh
Set-Content -Path $p -Value $key -Encoding ascii                # ascii: no BOM
icacls $p /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
Restart-Service sshd
```

- **An administrator's key goes in the program-data file, not the profile.** The stock `sshd_config`
  ends in a `Match Group administrators` block that redirects that group to
  `__PROGRAMDATA__/ssh/administrators_authorized_keys`, so a key written into the profile is ignored,
  with no message.
- **`icacls` is not decoration.** A file that non-administrators can write is refused, silently: the
  symptom is a password prompt with the correct key in the correct file.
- **A BOM breaks the line.** `Set-Content -Encoding ascii`, not `Out-File`, and not `Add-Content` onto a
  file that is already UTF-16. The first byte should be `115`, the `s` of `ssh-ed25519`.

To tell a client problem from a server one: `ssh -v` prints `Offering public key: …`, and
`Get-WinEvent -LogName 'OpenSSH/Operational' -MaxEvents 10` names the file sshd consulted and why it did
not use it. On this machine, `Invalid user` in that log means the **account name**, not the key — see
above. All four are now working on the 5090: the key is accepted, `docker` answers, and a container sees
the GPU (`nvidia-smi -L` inside `nvidia/cuda:12.4.0-base-ubuntu22.04`).

**Password authentication stays enabled on purpose.** The machine is on an overlay whose reachability
the event already accepts, and a colleague who wants a shell should not have to exchange keys first.
What that accepts is that the account password is now one way into a machine that will also hold a
`ghcr.io` read token — so it should be one of its own rather than the password the account is normally
signed in with. One trap: if the account signs in with a **PIN**, SSH still asks for the account's
*password*, and a wrong guess there looks exactly like a wrong password.

## Adding a machine

1. Put it on one of the LANs above, and follow the recipe for its OS.
2. Get the model's image onto it — build it there from the container folder's archive (the container
   repository's README, which is private, carries the command), or load a saved one — then read its
   declaration and check the machine can take it: `docker image inspect` for the label,
   `nvidia-smi --query-gpu=index,memory.free --format=csv` for the cards.
3. Start it detached, publishing on the LAN, **with the value the host already holds in `env`**:
   `docker run -d --restart unless-stopped -p 12711:12711 -e BACKEND_TOKEN=… --name <model> <image>`.
   A machine started with a different value is worse than a machine that is absent: it answers the
   requests that reach it with `401`, and that stops the model's whole list from working.
4. Prove it in this order: `docker logs` for the admission line and for `ready`, then **from the host**
   `curl -s http://<private address>:12711/health`, and `nc -vz <private address> 12711` if that fails.
   Only then wire it up.
5. Add that address to the model's list on the host and add this machine's row above. **The list's order
   decides which backend answers**, because routing is first-healthy-wins: the second entry is a fault
   domain, not extra capacity.

## How to re-measure a row

```bash
# who advertises which /24 — the answer is in AllowedIPs, and only there
ssh -i ~/.ssh/gwhs-demo.pem <user>@gwhs-host \
  'tailscale status --json' | python3 -c 'import json,sys; [print(a, v["HostName"], v["TailscaleIPs"][0]) for v in json.load(sys.stdin)["Peer"].values() for a in v["AllowedIPs"] if a.startswith("192.168")]'

# reachability, run on the host: the subnet routes live in their own table, so `ip route` shows none
ssh -i ~/.ssh/gwhs-demo.pem <user>@gwhs-host \
  'ip route get lab-3090; nc -vz lab-3090 12711'
```

Two traps this already cost us once each: a **refused port** is not an unreachable host (the laboratory
3090 refused 22 that morning with no `sshd` listening and took a key at 22 the same afternoon once it
was enabled), and **a missing ping reply** is not one either (the Windows machine blocks ICMP while
answering HTTP). Ask for a TCP connection, and ask the machine what it listens on (`ss -lnt`) before
concluding anything about the path.
