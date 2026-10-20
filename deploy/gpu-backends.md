# The compute plane's backends

How a machine becomes a backend: reach it, install Docker, run the model's container, and put its
address in the gateway's list. **Which machines exist, and who may reach them, is not this file** —
[`../.agents/rules/backend-pool.md`](../.agents/rules/backend-pool.md).

Three things this file does not argue, and only links:

- why routing, failover and the admission arithmetic are what they are —
  [`../.agents/rules/gateway.md`](../.agents/rules/gateway.md) and
  [`../.agents/references/2026101612_model-vram-and-latency-reference.md`](../.agents/references/2026101612_model-vram-and-latency-reference.md)
- what a container must answer — [`../.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md)
- how the room routers carry these addresses, and what each path measured —
  [`../.agents/references/2026101610_backend-reachability-over-the-room-routers.md`](../.agents/references/2026101610_backend-reachability-over-the-room-routers.md)

## 1. The path, before any software

**These are private addresses on the UTokyo Field Phenomics Lab tailnet, and only the gateway host
can reach them.** Each room's router is a tailnet node advertising its own `/24`; the gateway host
accepts those routes, so the address of `lab-5090` resolves from that host and from no other machine. Nothing
is published to the internet and no port is forwarded. A reader outside this lab who tries one of
these addresses gets a timeout — that is the design, not a fault.

What makes a machine reachable is therefore two facts, and neither is a client on the machine:

1. It sits on one of those LANs, behind a router that advertises the `/24`.
2. **A container publishes its port on the LAN interface**, which is `docker run -p <the model's
   port>:12711`.
   The default binding is `0.0.0.0`, and that is what the router forwards into.

Binding to `127.0.0.1` and publishing it with `tailscale serve` is the *alternative*, needing a
tailscale client on the machine; this pool does neither. On Docker Desktop the port is published by a
proxy running on **Windows**, which is exactly the interface the router forwards to. Inside WSL2
without Docker Desktop it is not, and LAN access would need `netsh portproxy` or mirrored networking —
the reason the Windows machine uses Desktop.

Conventions that hold everywhere and are not re-decided per machine:

- **A model's port is the model's, and the same number on every machine.** `12711` is
  `sam3.1-meta`, `12712` is `gwfss-segformer-b4`, wherever they run. Inside a container the port is
  always `12711` — each container has its own network namespace, so two models on one host do not
  collide there — and the host side carries the model's number. It sits outside both ephemeral
  ranges (`32768–60999` on Linux, `49152–65535` on Windows), and it is deliberately **not 8000**,
  which is the gateway's own socket on its host.
- **A card is admitted against the card minus 1 GB**, never against the card — the desktop drives the
  same display. For scale, the demo model holds 8.5 GB resident.
- **One router is one fault domain.** So however many machines the laboratory holds, they fail
  together; home is independent of it, and a rented `eu-central-1` instance — the gateway's own VPC,
  no tailnet — would be a third. That path is designed and not built, and it is the only one whose
  availability is contracted rather than borrowed.

### Reaching a machine to look at it

Every administrative connection goes **through the gateway host**, because it is the only machine
that accepts the routes. Both `HostName` values below are in `.agents/local/pool.md`. A workstation
on the laboratory LAN routes the backend's address to its own default gateway and gets nowhere, so a
backend is unreachable "from the lab" while sitting in it.

    Host gwhs-host
      HostName <the host's address>
      User admin
      IdentityFile ~/.ssh/gwhs-demo.pem

    Host lab-5090
      HostName <the 5090's address>
      User "Field of Phenomics"
      IdentityFile ~/.ssh/lab-5090
      ProxyJump gwhs-host

**The account name is not the profile directory name, and on Windows they usually differ.** The
5090's key sits in `C:\Users\45952` but the account is `Field of Phenomics`; sshd resolves the
account, so sending the directory name answers `Invalid user 45952` before it looks at any key —
which reads exactly like a rejected key. Ask the machine with `whoami` (`machine\account`). A name
with spaces needs double quotes, as above.

On **Linux**, `sshd` is normally installed and only needs enabling: `systemctl enable --now sshd`.
**Windows ships no SSH server at all** — §2.2 installs one.

### Checking a path

Run both on the gateway host, and read the answer rather than trusting the shape of the address:

```bash
# who advertises which /24 — the answer is in AllowedIPs, and only there
tailscale status --json | python3 -c 'import json,sys; [print(a, v["HostName"], v["TailscaleIPs"][0]) for v in json.load(sys.stdin)["Peer"].values() for a in v["AllowedIPs"] if a.startswith("192.168")]'

# reachability: the subnet routes live in their own table, so `ip route` shows none of them
ip route get <address>; nc -vz <address> 12711
```

Two readings that already cost us a wrong theory each: **a refused port is not an unreachable host**
(the 3090's `sshd` was off while three other ports answered), and **no ping reply is not a dead
machine** (the Windows host blocks ICMP and answers HTTP). Ask for a TCP connection, and ask the
machine what it listens on (`ss -lnt`) before concluding anything about the path.

## 2. Docker on the machine

Both recipes end in the same three facts: Docker runs, the GPU is visible **inside a container**, and
the port answers **from the gateway host**. Prove them in that order — a failure in the third is a
network question, and a failure in the second has nothing to do with the network.

### 2.1 Linux (Manjaro/Arch and Debian/Ubuntu)

Only the package line differs between distributions. Everything else — the toolkit, the runtime it
registers with Docker, the boot service, the group — is the same, and all of it needs `sudo`.

**Arch family (the laboratory 3090, the home 4090)**

```bash
sudo mhwd -a pci nonfree 0300                       # Manjaro's own driver install; plain Arch: nvidia-dkms
sudo pacman -S --needed docker nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl enable --now docker                  # a real boot service: no login required
sudo usermod -aG docker "$USER"                     # then log out and back in once
```

**Debian family** — NVIDIA's own archive, because the toolkit is not in the distribution's

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**Without the toolkit, `--gpus all` fails rather than falling back to the host's driver:**

```
$ docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi -L
docker: Error response from daemon: failed to discover GPU vendor from CDI: no known GPU vendor found
```

That is *not* a driver problem — `nvidia-smi` on the host is fine and the same container without
`--gpus all` starts normally. It is the toolkit alone that is missing. On some Manjaro branches the
package comes from the AUR; if the package line fails, that is the first thing to check.

Two things that bite after the install:

- **`nvidia-ctk runtime configure` rewrites `/etc/docker/daemon.json`.** If the machine keeps its
  Docker storage elsewhere — `{"data-root": "/home/…"}`, how a small root partition is survived —
  check that key is still there. If a merge drops it, Docker starts using `/var/lib/docker` and every
  image *looks* as if it vanished.
- **`systemctl restart docker` stops whatever was running**, and `--restart unless-stopped` brings
  each container back by itself. That costs one lost request, not nothing.

Then prove the two facts that matter:

```bash
nvidia-smi                                                     # the host sees the card
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi -L   # a container sees it
```

**Do not copy the Windows driver notes here:** on Windows the driver comes from the host OS, and on
Linux it must be installed and the toolkit configured, or `--gpus all` finds nothing.

### 2.2 Windows 11

This is the sequence that works on the laboratory 5090, end to end. Docker Desktop, not `apt install
docker.io` and not snap: inside WSL2 the package-manager dockers publish their ports *inside the WSL
VM*, where the router cannot reach them.

1. **Update the NVIDIA Windows driver** if it predates the 570 branch — a 50-series card needs it, and
   an old driver leaves `nvidia-smi` unable to see the card *inside WSL*.
2. Install **Docker Desktop** (the Microsoft Store build works — it has no "WSL 2 based engine"
   checkbox any more, because WSL2 is now the only engine).
3. **Settings → Resources → WSL Integration** → enable for the distro. Without it `docker` does not
   exist inside WSL, and the error looks like a missing Ubuntu package
   (`Command 'docker' not found, but can be installed with: sudo snap install docker`) — which it is
   not.
4. **Never install a Linux NVIDIA driver inside WSL.** The driver comes from Windows through
   `/usr/lib/wsl/lib`.
5. Verify in WSL: `nvidia-smi`, then `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04
   nvidia-smi -L`.
6. **Turn on an SSH server**, if the machine is to be administered over the tailnet rather than
   from its own chair:

   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Start-Service sshd
   Set-Service -Name sshd -StartupType Automatic
   New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server" -Enabled True -Direction Inbound `
     -Protocol TCP -Action Allow -LocalPort 22 -RemoteAddress <the 5090's LAN>/24,100.64.0.0/10
   ```

   `-RemoteAddress` is the **source**; both ranges are there because the router rewrites it to the
   local subnet in practice (sshd logs the Docker Desktop proxy, which is not the caller). **Never widen it to
   `Any`** — that is the public internet.

7. **Write the administrator's public key to the program-data file**, not the profile, then restart
   sshd:

   ```powershell
   $key = 'ssh-ed25519 AAAA… lab-5090 backend'
   $p = "$env:ProgramData\ssh\administrators_authorized_keys"      # NOT %USERPROFILE%\.ssh
   Set-Content -Path $p -Value $key -Encoding ascii                # ascii: no BOM
   icacls $p /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
   Restart-Service sshd
   ```

   The stock `sshd_config` redirects the administrators group to that file, so a key in the profile
   is ignored with no message; `icacls` is not decoration (a writable file is refused silently); and
   a UTF-16 file or a BOM breaks the line — the first byte should be `115`, the `s` of `ssh-ed25519`.

8. **Publish the backend port.** Docker Desktop adds a firewall rule when it publishes one, so
   usually nothing to do. If a published port does not answer, the rule is:

   ```powershell
   New-NetFirewallRule -DisplayName "biohack backends" -Direction Inbound -Protocol TCP `
     -LocalPort 12711,12712 -RemoteAddress the 5090's LAN,100.64.0.0/10 -Action Allow
   ```

   One rule lists every model port the machine publishes; a machine that adds a model adds its
   number here, not a second rule that quietly differs.

9. **Windows blocks ICMP echo by default**, so a machine that answers nothing to `ping` is not
   necessarily down. Never use ping as the reachability test for this backend — use a TCP probe.

10. The `docker` command works in WSL and, in a *newly opened* PowerShell, in Windows too. One daemon
    serves both, and which shell created a container has no bearing on its restart policy.

The traps behind these steps — the NAT rewriting the source, `Invalid user` meaning the account name,
the credential helper that fails an anonymous pull over SSH, the BOM — each cost an hour once and are
recorded in
[`../.agents/references/2026101621_deploying-sam31-on-the-5090.md`](../.agents/references/2026101621_deploying-sam31-on-the-5090.md).

**Password authentication stays enabled on purpose.** The machine is on an overlay whose reachability
the event already accepts, and a colleague who wants a shell should not have to exchange keys first.
That makes the account password one way into a machine that also holds a `ghcr.io` read token, so it
should be one of its own rather than the password the account is normally signed in with. If the
account signs in with a **PIN**, SSH still asks for the account's *password*.

## 3. The image, on the machine

### 3.1 Get it there

The containers are not in this repository: `models/` is a submodule pointing at the private one, and
its `README` is the authority for the archive and the build. The short version, run in `models/`:

```bash
git archive --format=zip --prefix=sam31_official/ \
    -o build/sam31_official-$(git rev-parse --short HEAD).zip HEAD:sam31_official

scp build/sam31_official-*.zip lab-5090:sam31_official.zip    # the ssh alias carries the jump host
```

Then on the backend: unzip, and `docker build -t sam3.1-meta sam31_official`. The build fetches its
own checkpoint on the machine's own downlink — the archive is 2.1 MB and the build about 3.5 GB — so
one archive is what a new machine needs, not a 14 GB image over one sender's uplink.

Two facts about `docker build` that are not obvious:

- **`docker pull` is not the only thing the credential helper breaks.** On Windows over SSH, the
  build dies at `COPY --from=ghcr.io/astral-sh/uv` with `error getting credentials … A specified
  logon session does not exist` — while `docker manifest inspect` against the same registry, from the
  same session, succeeds. Point the CLI at an empty config directory and no credential lookup
  happens: `DOCKER_CONFIG=/tmp/dcfg docker build -t sam3.1-meta sam31_official`. Every image a model
  container pulls is public, so nothing needs authenticating.
- **A build outlives an SSH session only if you make it.** Run it under `nohup`/`tmux`, or expect a
  dropped connection to kill twenty minutes.

`docker save | docker load` is the alternative for a machine with no route to the weight store; it
costs tens of gigabytes over the sender's uplink and nothing else.

### 3.2 The run line

One container per model, named after the model id it serves. **The parameters live in the container,
not in the image**, which is why `docker run` is written out in full:

| flag | stored as | read it back with |
|---|---|---|
| `--name <model>` | the container's name | `docker ps -a` |
| `--gpus all` | `HostConfig.DeviceRequests` | `docker inspect --format '{{.HostConfig.DeviceRequests}}' <model>` |
| `--restart unless-stopped` | `HostConfig.RestartPolicy` | `docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' <model>` |
| `-p <the model's port>:12711` | `HostConfig.PortBindings` | `docker port <model>` |
| `-e BACKEND_TOKEN=…` | `Config.Env` | `docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' <model>` |
| `-e SAM31_MACHINE=…` | `Config.Env` | the same `docker inspect` |

    docker run -d --gpus all --restart unless-stopped -p <the model's port>:12711 \
      -e BACKEND_TOKEN=<the value in the gateway host's env> \
      -e <the image's machine variable>=<this machine's name> --name <model> <image>

- `-e BACKEND_TOKEN=…` is **mandatory** — the entrypoint refuses to start without it — and it must
  equal the gateway host's `BIOHACK_BACKEND_TOKEN`. A different value is not a stale backend, it is a
  dead model: it answers `401` and the gateway abandons the whole model rather than trying the next
  machine (§4).
- `-e SAM31_MACHINE=…` is **what every answer calls this machine.** It is copied into `computed_by` in
  `/health` and in every prediction, so a participant comparing two answers can see they came from
  different cards — and an operator reading one can tell which host said it. Left unset, a container
  reports its own host name, which in Docker is a random id. A name is not an address and not a
  capacity: the record holds the name, the card, its compute capability, and the CUDA and torch
  versions, and nothing else.
- `--gpus all` is what puts the card inside the container; without it the entrypoint's admission
  check refuses to start, which is it doing its job.
- `--restart unless-stopped` is what brings the backend back after a reboot. Never `always`: the
  entrypoint refuses a card it cannot fit, and `always` turns that refusal into a loop.
- `-p <the model's port>:12711` publishes on the LAN. Do not bind loopback, and never publish two
  models on one host port: the gateway's list, the router and the firewall rule all name that
  number, and a model that borrowed another's would answer under the wrong name until the first
  call.

`docker start` needs none of it — everything is already recorded, which is why stop/start is safe and
why a *changed* value means creating the container again. **Only the restart policy can be changed in
place** (`docker update --restart unless-stopped <model>`); an environment variable, a port, the GPU
access or the name cannot, and each needs `docker rm -f` and the run line again.

### 3.3 Started is not ready

    docker ps                      # what is running
    docker ps -a                   # including anything you stopped
    docker logs --tail 40 <model>  # what it printed, including why it refused to start
    docker stop <model>            # stop it; it stays stopped
    docker start <model>           # start it again, with everything it was created with
    docker restart <model>         # stop and start, which is not the same as re-creating it

`/health` answers `503` while the checkpoint loads and one throwaway prompt runs; `docker logs` prints
`budget ok: needs … GB` and then `ready: load … s`, and only then does it serve. The gateway treats a
`503` as "still loading" and asks again, so a restart costs the pool seconds.

`/health` also answers the machine's `computed_by` — the name above, the card, its compute capability,
and the CUDA and torch versions. It is the same record every prediction carries, so a backend that
answers differently from its neighbour is comparable in one field instead of by inference. Two of these
machines do answer one call differently: `gwfss` row 4 at `confidence_threshold` 0.25 returns 18 objects
on the 3090 (`8.6`, Ampere) and 17 on the 5090 (`12.0`, Blackwell), with the same image, prompt, CUDA and
torch on both — see
[`../.agents/references/2026101711_two-backends-differ-on-the-same-call.md`](../.agents/references/2026101711_two-backends-differ-on-the-same-call.md).

**A new image is three acts, and the third is the one forgotten.** Build it, **re-create** the
container (`docker rm -f` then the run line — an image change is never a `restart`), and **restart the
gateway after the containers**. A backend's declaration is read once, at the gateway's first contact,
and is never re-read while the process lives: a container that now declares five parameters, behind a
gateway that cached two, publishes two, validates against two, and refuses `boxes` as an unknown name.
Measured 2026-10-18, during the parameter-surface deploy: all three backends were rebuilt and running,
`GET /models` still showed the old declaration, and a `boxes` call answered `400` until the gateway was
restarted.

**Prove it from the gateway host, not from the machine.**
`curl http://<private address>:12711/health` is the path a participant's request takes; from the
machine itself, `127.0.0.1` proves only that the container listens — on Windows that is a different
interface from the one the router forwards to.

### 3.4 Handing the card back

The machines are workstations, so the card is borrowed rather than owned. `docker stop <model>` gives
it back: the container stays stopped (that is what `unless-stopped` means) and no request reaches it.
Everything needed to bring it back is in the container, so `docker start` later is the whole undo.

**A stopped backend that is still in a model's list costs the pool one wait.** The first request that
meets it pays the connect bound (~3 s) and is then answered by the other machine; the request after
each 30-second window pays it again. If the machine will be down for a while, remove its address from
`models.json` on the host and restart the gateway (§4) — one line, and no second copy of the truth.

### 3.5 Surviving a reboot, unattended

This is the part that fails during an event and looks like a network fault.

- **`docker pull` is a one-time act, not a boot-time one.** `--restart unless-stopped` restarts a
  container that exists; it never re-pulls. Moving to a new image is a deliberate act.
- **On Linux the chain is one link:** `systemctl enable --now docker`, then the restart policy.
- **On Windows the chain has a weak link.** Docker Desktop is a user-level program, so with nobody
  logged in after a reboot the engine never starts and no policy can help. Settings → General → *Start
  Docker Desktop when you sign in* must be paired with **Windows automatic sign-in**, plus sleep and
  hibernation off and Windows Update reboots deferred for the event window.
- Verify by actually rebooting the machine and watching the container come back. It is the only test
  that finds a broken auto-sign-in, and it is cheap now.

## 4. On the gateway host: putting it in a model's list

**The list is host configuration, not repository content** — it names running machines. It is
`models.json` in `~/.config/biohack-gateway/`, beside the token pool, one entry per model:

    {"sam3.1-meta":        ["http://<address>:12711"],
     "gwfss-segformer-b4": ["http://<address>:12712"],
     "yolo":               ["http://<address>:12713", "http://<address>:12713"]}

A file that does not parse means **no models**, never all of them, so a typo in host configuration
cannot become a gateway that answers for models nobody configured. **Order is preference:** routing
is first-healthy-wins with idle backends preferred, so the second entry is a fault domain first and
extra capacity only incidentally.

**Its twin is the token, and the value has to be transcribed from `env`, never from a message.** One
character is a different value, and `/health` cannot show the difference: it needs no token, so a
mismatched end still declares itself and `GET /models` still reads `backends_up: 2` while every
request that reaches it fails. The check is one request, with the candidate in a shell variable on
the host:

    curl -s -o /dev/null -w '%{http_code}\n' -X POST http://<backend>:12711/models/<model>/predict \
      -H "X-Backend-Token: $T" -H 'Content-Type: application/json' -d '{}'

`401` carrying `bad or missing X-Backend-Token` is the value being wrong. **Any other 4xx means the
token passed** and the deliberately empty body is what was refused — which is why the probe sends a
body it knows is invalid: it never runs an inference.

**A mismatch does not fall through to the next backend — it takes the model down.** The gateway reads
`401`/`403` from a backend as *its own* credential being refused, logs `a backend refused the
gateway's token`, answers `503`, and never tries the model's other backends. Rotating the value
therefore means editing `env` **and** restarting every container on every model's list; there is no
second copy, and the value never enters the repository.

Adding a machine is then five steps:

1. Follow §1–§3; the machine has Docker, a container sees the card, and `12711` answers **from the
   gateway host**.
2. Read the container's own declaration and check the card can take it:
   `docker image inspect` for the label, `nvidia-smi --query-gpu=index,memory.free --format=csv` for
   what is free, and `budget >= resident + per_request` from
   [`../.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md)
   against the figures in
   [`../.agents/references/2026101612_model-vram-and-latency-reference.md`](../.agents/references/2026101612_model-vram-and-latency-reference.md).
3. Start it detached with the value the host already holds, then pass the probe above.
4. Add `http://<private address>:12711` to the model's list in `models.json` on the host and restart
   the gateway so it reloads the file.
5. Add the machine's row to
   [`../.agents/rules/backend-pool.md`](../.agents/rules/backend-pool.md) — status, not a narrative.

Verify what the pool thinks: `GET /models` with a participant key reports `backends_up` per model,
and `GET /models/{model}` the declaration that backend sent. `backends_up` counts declarations rather
than working ends — a mismatched token still counts — so the probe in step 3 is what closes the gap.

## 5. Where the rest is written down

| What | Where |
|---|---|
| Which machines exist, who may reach them, one router is one fault domain | [`../.agents/rules/backend-pool.md`](../.agents/rules/backend-pool.md) |
| The room routers, the exit node, the latencies each path measured | [`../.agents/references/2026101610_backend-reachability-over-the-room-routers.md`](../.agents/references/2026101610_backend-reachability-over-the-room-routers.md) |
| The 5090's bring-up: the credential helper, the token mismatch, the failover that took 61.2 s | [`../.agents/references/2026101621_deploying-sam31-on-the-5090.md`](../.agents/references/2026101621_deploying-sam31-on-the-5090.md) |
| The 3090 becoming a container, and the toolkit that was missing | [`../.agents/references/2026101709_the-3090-becomes-a-container.md`](../.agents/references/2026101709_the-3090-becomes-a-container.md) |
| Why the port is `12711` and what moving it cost | [`../.agents/references/2026101622_moving-the-backend-port-to-12711.md`](../.agents/references/2026101622_moving-the-backend-port-to-12711.md) |
| What a container answers, and where normalisation belongs | [`../.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md) |
| What the gateway's resident and per-request figures rest on | [`../.agents/references/2026101612_model-vram-and-latency-reference.md`](../.agents/references/2026101612_model-vram-and-latency-reference.md) |
| Routing, timeouts and the token, as invariants | [`../.agents/rules/gateway.md`](../.agents/rules/gateway.md) |
| The containers themselves and the archive a machine receives | the submodule's `README` (private) |
