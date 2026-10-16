# The backend port moves to 12711

2026-10-16, late evening. The pool's inference port was **8000**; it is now **12711**, inside the
container and on the host alike. The decision is in
[`deploy/gpu-backends.md`](../../deploy/gpu-backends.md); this is what moving it cost and what is still
outstanding.

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

**The 5090, and no rebuild was needed.** `serve.py` reads the port from `SAM31_PORT`, so overriding the
image's baked value is enough:

    docker rm -f sam3.1-meta
    docker run -d --gpus all -p 12711:12711 --restart unless-stopped \
      -e SAM31_PORT=12711 \
      -e BACKEND_TOKEN=… --name sam3.1-meta sam3.1-meta

Two things verified rather than assumed: **Docker Desktop's firewall rule follows the published port by
itself** (`/health` answers 200 on `192.168.253.91:12711` from the gateway host, and the old `:8000`
answers `000`), and the image's `EXPOSE 8000` is cosmetic — it stays 8000 until the next build, which
only `docker run -P` would ever have read.

**`models.json` on the host now holds two ports, on purpose:**

    {"sam3.1-meta": ["http://192.168.253.91:12711", "http://192.168.250.228:8000"]}

The 3090's entry is still `8000` because that is where the 3090's container actually listens until it is
recreated too. Flipping it first would have made a *listed* backend that nothing answers on, which is
the worst of the three states: the request that meets it waits the connect bound, and the journal blames
a machine that is in fact fine. **Outstanding: recreate it, then flip that one entry and restart the
gateway.**

    # on labpc, as the user in the docker group (or with sudo)
    docker rm -f sam3.1-meta
    docker run -d --gpus all -p 12711:12711 --restart unless-stopped \
      -e SAM31_PORT=12711 \
      -e BACKEND_TOKEN=<the same value the host's env holds> \
      --name sam3.1-meta sam3.1-meta

## Verified after the move

`backends_up: 2`, `max_in_flight: 2`, and a `predict` on `gwhs-temp` row 0 answering 200 in 1.24 s with
one object (area 173716), served by the 5090 — its own log shows the POST. The 3090 declared itself on
the old port throughout, which is why the list could carry both without a lie in it.

## Also done in the same pass

[`gpu-backends.md`](../../deploy/gpu-backends.md) gained **"Controlling a container on a machine you are
sitting at"**: which flag is stored where in a container's configuration (`docker inspect` reads them
back), that only the restart policy can be changed in place and everything else means `docker rm -f` plus
the run line, what the five lifecycle commands are, why `started` is not `ready`, why the check has to be
run *from the gateway host*, what leaving a backend stopped while it is listed costs, and the Windows
(Docker Desktop GUI controls, CLI creates) and Linux (systemd, the docker group, no GUI at all)
differences. The root README gained a short "GPU backends" summary pointing at it.

Two things there are **not** verified and say so: Docker Desktop's run dialog fields, and
`docker update --restart` (documented, untried here).
