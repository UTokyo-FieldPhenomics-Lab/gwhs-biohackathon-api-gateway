# Deploying the sample container on the 5090

2026-10-16, 19:56–21:06 JST. The laboratory 5090 built `sam3.1-meta` from the container folder's
archive, ran it against its own GPU, and became **the second backend in the gateway's list** — the
end of the chain the plan opened
([`../plans/2026101619_✅weights-fetched-inside-the-build.md`](../plans/2026101619_✅weights-fetched-inside-the-build.md))
and the first time the weight-fetch design met a machine other than its author's. The rules it moved
are [`../rules/models.md`](../rules/models.md); what the machines are is
[`../../deploy/gpu-backends.md`](../../deploy/gpu-backends.md).

## Did the design survive a second machine

Yes, and the numbers are the argument for it:

| Step | Observed |
|---|---|
| `scp` of `sam31_official-b4f5889.zip` | 2.1 MB, instant |
| base layer pull, Docker Desktop's own downlink | 2.06 GB in 129 s (~16 MB/s) |
| `uv sync --frozen` inside the build | torch + nvidia wheels, ~4 GB |
| **the fetch this session added** | **3.50 GB in 3 m 33 s, `#15 DONE`, zero `ERROR` lines** |
| resulting image | `sam3.1-meta:latest`, 13.9 GB |
| container on the card | admission `needs 5.5 GB, budget 30.8 GB`, `ready: load 10.8 s, warm 1.2 s` |
| through the gateway | `GET /models` → `backends_up: 2`; `predict` → 200 in 0.9 s, `gwhs-temp` row 0, one `wheat head` at 0.52, area 173716, RLE 2184 chars |
| what it left on disk | 36.4 GB of images and 5.7 GB of build cache (`docker system df`) |

The byte assertion did not fire, which is the interesting negative: the same 3.50 GB gdown reports is
what the `ARG` expects, so the assertion is a truncation check and not a tripwire that goes off on a
good build.

## What broke, in the order it did

**1. `docker build` hits the Windows credential helper, not only `docker pull`.** The build died on
`COPY --from=ghcr.io/astral-sh/uv:0.12` with `error getting credentials - err: exit status 1, out: A
specified logon session does not exist. It may already have been terminated.` A minute earlier,
`docker manifest inspect ghcr.io/astral-sh/uv:0.12` from the same SSH session had answered 200 — so that
check reads as a green light and is not one. The fix that leaves the machine alone:

```bash
ssh gwhs-5090 'wsl -e bash -lc "cd /mnt/c/Users/45952 && DOCKER_CONFIG=/tmp/dfcfg \
  nohup docker build --progress=plain -t sam3.1-meta sam31_official > /tmp/sam31-build.log 2>&1 &
  disown"'
```

`DOCKER_CONFIG` pointing at an empty directory skips the credential lookup entirely, and every image the
container pulls is public, so there is nothing to authenticate. **The build must be detached**: it takes
20 minutes and an SSH session that drops takes the build with it (`nohup … & disown` inside WSL
survives). `--progress=plain` because gdown's `\r` progress otherwise makes the log unreadable — read it
with `grep -a ERROR` and `tail -c`, never with a pattern containing a quote, because the command is
passing through `cmd.exe` on the way to WSL.

**2. The gateway host could not take a backend at all.** It was at `870c379`, 28 commits behind main,
with no `models.py` in its installed package — no model surface, so nothing to register the 5090 into —
and its `env` held only the template comments. `git pull` plus the documented update path
(`sudo ./deploy/setup-gateway.sh`) fixed it, and the journal line to look for is:

    INFO biohack_gateway.models: no model-backend list at /home/admin/.config/biohack-gateway/models.json;
    the model surface has no models

That line is proof the surface is live and reading the right path. It is also the state the host is in
until the first backend is written into that file.

**3. That update broke the service, and the fault was latent in the unit.** The first run of the update
path after the unit's `InaccessiblePaths=` was changed to `%h/.config/biohack-gateway/publish` produced

    biohack-gateway.service: Failed to set up mount namespacing:
    /root/.config/biohack-gateway/publish: No such file or directory
    Main process exited, code=exited, status=226/NAMESPACE

`%h` is the home of the user running the *manager* — `/root` under the system manager — and systemd's own
man page says so and adds that it is **not influenced by `User=`**. Read on the host with
`man systemd.unit | sed "s/.\x08//g" | grep -A5 "User home directory"`; the backspace-stripping matters,
because a plain grep for that sentence finds nothing. Fixed in `deploy/systemd/biohack-gateway.service`
as a literal path, with `setup-gateway.sh` rewriting it alongside `User=` and `EnvironmentFile=`. **The
lesson is about the update path itself**: the unit on disk had been correct until someone re-ran the
script, so "re-running it is the update path" needs to be *run* once after any change to that file —
this one had never been exercised since the `%h` edit.

**4. The 3090's token was not the one that was in hand.** The 5090 and the host were configured with the
value the earlier session settled on, and the 3090 answered `401 bad or missing X-Backend-Token` to it —
with the literal and the variable form both, so the header was not the problem. Nothing in the repository
or on the host holds that value (the 3090's container environment does, and it is not reachable from the
host). What matters for the design is the consequence, which is now a rule in
[`gpu-backends.md`](../../deploy/gpu-backends.md): the gateway reads `401` from a backend as *its own*
credential being refused and answers `503` **without trying the other backends**. So the 5090 is listed
first, and the 3090 becomes a real fault domain only once it is restarted with the same value.

## The order of the list, and a correction

The first draft of this deployment recommended the 3090 first, on a warm-prompt figure of 0.34 s against
the 5090's 0.909 s. That comparison does not survive: the 5090 measured 0.909 s for a dry run and 1.21 s
for the real container, and a real request through the gateway came back in 0.90 s end to end. The two
cards have never been measured on the same frame, so neither is ranked here — the 5090 is listed first
because the token it holds is the one the host holds, and because it is the machine that was verified
today.

## Open, and worth doing before the event

- **The resident figure understates the 5090.** The label and `/health` say `resident_gb 3.7` from a
  3812 MiB measurement on the 3090; on the 5090 the same container left ~7.7 GB used with ~1.1 GB of
  desktop, so roughly **6.5 GB**. Admission is unaffected at a 31 GB budget, but the number is what a
  human or the deploy script reads first, and `resident_gb` measurements belong per card.
- **Restart policy: the container is running `--restart unless-stopped`, and the Dockerfile comment says
  no restart policy anywhere.** `gpu-backends.md` is what asks for `unless-stopped` (a backend must come
  back after a reboot with nobody logged in; on Windows, Docker Desktop needs a signed-in user as well),
  so the deploy side is right and the comment in `sam31_official/Dockerfile` was the thing that needed
  saying precisely.
- **The 5090's load prints `missing_keys=['backbone.vision_backbone.convs.3.conv_1x1.weight', …]`.** Same
  weights and same code as the 3090's verified measurement, so it is not a property of this machine —
  but nothing here records whether it is expected, and the first person to see it will think it is.
- **The build left 36 GB of images and 5.7 GB of cache on the 5090.** Nothing reclaims it automatically;
  the earlier session on that machine ended by freeing the same kind of space by hand.
