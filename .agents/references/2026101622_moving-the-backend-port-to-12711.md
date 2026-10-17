# The backend port moves to 12711

2026-10-16, late evening. The pool's inference port was **8000**; it is now **12711**, inside and outside alike. The decision is in
[`deploy/gpu-backends.md`](../../deploy/gpu-backends.md); this is what moving it cost, and what it
revealed.

## Why the number changed

Two reasons, and the second is the one that decided it:

- 8000 is a port everything uses, so a backend on a workstation competes for it with whatever else the
  person sitting there runs.
- **The gateway's own socket is `127.0.0.1:8000`.** One number meaning two different things in one
  document is how someone ends up publishing a backend on the gateway's port; the pool's convention and
  the host's implementation had no reason to share a number.

12711 is not a service port, and it sits outside both ephemeral ranges that could take it from under a
container — Linux `32768–60999`, Windows `49152–65535`, both read off the machines today.

## What changed, and where the number lived

The blast radius was smaller than it looked. Everything in
[`deploy/gpu-backends.md`](../../deploy/gpu-backends.md) is one sweep: the convention line, both run
commands, the `models.json` example, the token probe, the `/health` example, the Windows firewall
fallback, "Adding a machine" and "How to re-measure a row". In the container repository it is four
lines: `SAM31_PORT` and `EXPOSE` in `Dockerfile`, the default in `serve.py`, the run line in
`README.md`.

**The gateway's own 8000 was deliberately left alone** — the systemd unit, `deploy/Caddyfile`, and the
`localhost:8000` examples in the root README are that other thing, and conflating them is what the
change is about. This file and the other two references from today keep their `:8000` commands as the
record of what was run, with the note that the port moved.

## Moving the machines

**The 3090 is the machine this work ran on, and its backend is not a container.** `crest-nerv`
(`192.168.250.228`) *is* the checkout the work was happening in, which is why reaching it over SSH had
been written off as impossible: the connection to `192.168.250.228:22` is refused because that is this
machine and its `sshd` is not listening, not because a router or a firewall was in the way. What serves
the pool there is a bare process from that checkout:

    $ ps -o pid,ppid,cmd -p 453878
    … /home/crest/Documents/Github/gwhs-biohackaton-api-gateway/models/sam31_official/.venv/bin/python3 serve.py

with `SAM31_PORT=8000` and the pool's `BACKEND_TOKEN` in its environment, `uv run python serve.py` as its
parent, output in `/tmp/opencode/labpc8000.log`, and **nothing supervising it** — no unit, no `--restart`,
so a reboot or the end of the shell that started it removes the pool's second backend in silence. It had
been up 3 h 47 m. Moving it is therefore a kill and a relaunch, not `docker rm`:

    SAM31_PORT=12711 BACKEND_TOKEN=… setsid nohup uv run python serve.py \
      > /tmp/opencode/labpc12711.log 2>&1 < /dev/null &

Reloaded in 7.52 s with `budget ok: needs 5.5 GB, budget 23.0 GB (15166 MiB free of 24576)` — the 3090
reserving its gigabyte as the rule says — and the `predict` route answers `400 input.url is required` to
an empty body on the new port, which is the token check passing.

**A second, stray instance was found and left alone.** `ss` showed two `serve.py` processes on that
machine: the one above, and a 6-hour-old one on `:8125` whose `BACKEND_TOKEN` is a `test-…` value that no
list points at. It holds several gigabytes of the card. Stopping it is someone's call, not a side effect
of this change.

**The 5090 needed no rebuild.** `serve.py` reads the port from `SAM31_PORT`, so overriding the image's
baked value is enough:

    docker rm -f sam3.1-meta
    docker run -d --gpus all -p 12711:12711 --restart unless-stopped \
      -e SAM31_PORT=12711 \
      -e BACKEND_TOKEN=… --name sam3.1-meta sam3.1-meta

Two things verified rather than assumed: **Docker Desktop's firewall rule follows the published port by
itself** (`/health` answers 200 on `192.168.253.91:12711` from the gateway host, and the old `:8000`
answers `000`), and the image's `EXPOSE 8000` is cosmetic — it stays 8000 until the next build, which
only `docker run -P` would ever have read.

**`models.json` on the host, both entries on the new port:**

    {"sam3.1-meta": ["http://192.168.253.91:12711", "http://192.168.250.228:12711"]}

## Verified after the move

`backends_up: 2`, `max_in_flight: 2`, and a `predict` on `gwhs-temp` row 0 answering 200 in 1.18 s with
one object (area 173716). Both backends declare themselves, and the 3090's own predict route passes its
token check on the new port.

## What this says about the documentation

The port sweep is done and the pool is consistent, but **the shape was not**: the section written the same
evening — "Controlling a container on a machine you are sitting at" — describes what a backend *should*
be, and the 3090 was not that. On that machine the whole reboot-survival story in
[`gpu-backends.md`](../../deploy/gpu-backends.md) ("once, and the policy carries it from then on") did
not apply at all: there was no container, so there was no policy. **Resolved the next morning**: the
machine now runs the container, and the reason it never had was a missing NVIDIA toolkit —
[`2026101709`](2026101709_the-3090-becomes-a-container.md).

## Also done in the same pass

[`gpu-backends.md`](../../deploy/gpu-backends.md) gained **"Controlling a container on a machine you are
sitting at"**: which flag is stored where in a container's configuration (`docker inspect` reads them
back), that only the restart policy can be changed in place and everything else means `docker rm -f` plus
the run line, what the lifecycle commands are, why `started` is not `ready`, why the check has to be
run *from the gateway host*, what leaving a backend stopped while it is listed costs, and the Windows
(Docker Desktop GUI controls, CLI creates) and Linux (systemd, the docker group, no GUI at all)
differences. The root README gained a short "GPU backends" summary pointing at it.

Two things there are **not** verified and say so: Docker Desktop's run dialog fields, and
`docker update --restart` (documented, untried here).
