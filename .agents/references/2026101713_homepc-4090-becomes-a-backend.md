# homepc's 4090 becomes a backend

Followed [`deploy/gpu-backends.md`](../../deploy/gpu-backends.md) §1–§3 on `homepc` after the guide
was written for the 3090 and the 5090. Everything in it held; the differences are recorded here.

## The path was already there

Measured from the gateway host, not from the laboratory workstation — `labpc-host` has no tailnet
client and routes the backend's address to its own default gateway, so "unreachable from the lab" says
nothing about the backend.

```
tailscale status --json          # home-router advertises the home LAN, online
ip route get <the 4090's address>    # dev tailscale0 table 52, src <the host's address>
```

| Probe | Before the container | After |
|---|---|---|
| ICMP | 255 ms, 0% loss | same |
| TCP 22 | open | open |
| TCP 12711 | **refused** | open |

The refused port before `docker run` and the open one after is the whole §2/§3 claim, measured.

## The machine was ready except for the image

Manjaro, RTX 4090 24564 MiB, driver `610.57.04`; Docker `29.7.2` already enabled and active;
`nvidia-container-toolkit 1.20.0` already installed, so §2.1 needed only the proof:

```
docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi -L
GPU 0: NVIDIA GeForce RTX 4090 (UUID: GPU-a277531e-…)
```

**`daemon.json` carries `"data-root": "/home/hwang/Applications/docker-lib"`** — the small-root-
partition case the guide warns about. Nothing ran `nvidia-ctk runtime configure` here, so the key was
never at risk; had it been, this is the machine where that trap fires. `/home` had 615 GB free.

## Access

`sshd` accepts `publickey,password`; the account is `hwang` (host `NERV-Linux`). The workstation's key
was not authorized, so the password was used **once** to append
`~/.ssh/id_ed25519.pub` to `~/.ssh/authorized_keys`; every connection since is key-based through
`ProxyJump gwhs-host`, now an alias `gwhs-homepc` in `~/.ssh/config`. The password is not stored
anywhere in this repository or on this machine.

`sshpass` is not installed on the workstation and `sudo` is not passwordless, so the one password
login used `SSH_ASKPASS` + `SSH_ASKPASS_REQUIRE=force` under `setsid -w` with a 0700 script deleted
immediately after.

## The build

The archive in `models/build/` was older (`8baa7a4`) than the submodule's HEAD (`89191e3`), so a fresh
one was cut; the diff between them touched only `.dockerignore`, `README.md` and the moved test, but a
stale archive name is exactly what the repository forbids. 2.2 MB over `scp`, unzipped on the machine.

- **Legacy builder, so no `--progress`.** `docker build --progress=plain` fails with
  `unknown flag: --progress` because buildx is not installed. Dropping the flag is the whole fix; the
  §1.1 note about the legacy builder committing every layer still applies and still costs nothing
  because `UV_NO_CACHE=1` is already in the Dockerfile.
- **Weights at 54 MB/s.** The 3.5 GB fetch measured ~55 MB/s from the home downlink, about a minute,
  with the byte-count assertion passing on the first try.
- **Finished image 14.5 GB**, against 24.6 GB for the same Dockerfile on the 3090 — the 3090's figure
  carries the legacy builder's intermediate layers, which is the note in the Dockerfile reading back
  as a measurement.
- Run detached with `setsid nohup … &`, because the build outlives an SSH session.

## The run line

`BACKEND_TOKEN` was read from the gateway host's `env` and passed to the machine **on stdin**, not on
the command line, so the value never appears in a process list or in a shell history:

    printf '%s\n' "$T" | ssh gwhs-homepc 'read -r T; docker run -d … -e BACKEND_TOKEN="$T" …'

`-p 12711:12711`, `--restart unless-stopped`, `--gpus all`, `-e SAM31_MACHINE=homepc-4090`. The
entrypoint admitted it and said so:

```
budget ok: needs 5.5 GB, budget 23.0 GB (19842 MiB free of 24564 MiB, 1.0 GB held back)
ready: load 6.42 s, warm prompt 0.78 s, in flight at most 1
```

`/health` from the gateway host answered `200` in **0.545 s** with
`machine homepc-4090, RTX 4090, cc 8.9, cuda 12.8, torch 2.10.0+cu128`. The token probe (empty body,
candidate token) answered **400** with `input.url is required` — the token passed, as §4 defines.

## It infers, and the port is the LAN's

The backend's own contract is `{"input": {"url": …}}`; the participant's `{"dataset", "row"}` is the
gateway's job to resolve, and a body in the participant's shape answers `400` on the backend.

| Call | lab 3090 | homepc 4090 |
|---|---|---|
| `gwhd` row 0, th 0.25 | 0 objects, 1.00 s | 0 objects, 0.64 s |
| `gwfss` row 4, th 0.25 | 18 objects, 0.79 s | **17 objects, 0.66 s** |

Zero objects on `gwhd` row 0 is the image, not the card: both backends agree. `gwfss` row 4 shows the
same 18-vs-17 split that
[`2026101711_two-backends-differ-on-the-same-call.md`](2026101711_two-backends-differ-on-the-same-call.md)
records for the 3090 and the 5090, so the 4090 behaves as a third card rather than as a broken one.
Absolute times are not a comparison of the cards — row 4 is a 512² tile and the RTT to home is 255 ms
in both directions.

## What was not done

- **No reboot test.** §3.5's chain is `systemctl enable --now docker` (already enabled) plus the
  restart policy, but it has not been observed bringing the container back on this machine.
- **Not added to `models.json`.** The gateway still lists the 5090 and the 3090 only, so nothing routes
  here yet; `backends_up` for `sam3.1-meta` is still 2.
- `models/build/` still holds the two older archives (`d22ccc9`, `8baa7a4`) beside the new one, contrary
  to the "current archive only" rule. They were left because deleting a submodule's scratch files was
  not this session's call.
