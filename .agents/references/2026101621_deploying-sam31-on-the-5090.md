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

**4. The token differed by one character, and `/health` could not show it.** The host and the 5090 were
configured with the value the earlier session's *quoted message* carried; the 3090's container had been
started, in that same session, with the same string **without its leading underscore** (42 characters
against 43). It answered `401 bad or missing X-Backend-Token` to the 43, and
`{"detail":"input.url is required…"}` — a body error, which only comes after the token check — to the 42,
which is how the two ends were told apart without reading the 3090's container: it is not reachable from
the host, which holds no key for it. Where each came from is in the stored session history: at 17:38 the
value was quoted *with* the underscore and the reply restated it *without*, and every command that was
actually executed afterwards — the `docker run` at 18:40 and the verification calls that followed — used
the 42.

Two things to carry forward, both now in [`gpu-backends.md`](../../deploy/gpu-backends.md): a backend
whose token differs does not fall through to the next one (the gateway reads `401` as its own credential
refused and answers `503` without trying the rest), and the mismatch is invisible from outside because
`/health` needs no token — both ends declared themselves, `GET /models` read `backends_up: 2`, and every
request simply went to the backend listed first. **Unified on the 42-character value**, which required no
change to the 3090 at all: one `sed` on the host's `env` and one container recreated on the 5090.

## Failover, measured once the two ends agreed

With the list as `[5090, labpc]` and the 5090's container stopped, the same `predict` came back **200 with
the identical geometry from labpc** — so the second entry is a real fault domain, which it was not an hour
earlier. It took **61.2 s**, and that number is the finding: the gateway waited a whole request timeout
(60) before trying the next backend, then passed the failed one over for only 30 s — shorter than the wait
it had just caused, so the request arriving after that window paid it again, and every request arriving
during the minute queued behind.

**Fixed the same evening**, as a relationship rather than a number — `gateway.md` carries the invariant
now, and the constants carry these measurements. Two corrections came out of doing it, both worth more
than the change itself:

- **The connect phase is what a dead backend costs here, not the response budget.** The first version left
  a window between reading the routing order and counting the attempt, and a live four-request burst
  showed two of three requests still queueing behind the stopped backend; making the two one step fixed
  it — **exactly one** attempt then met the failure, in **4.25 s** (the 3-second connect bound plus
  labpc's 1.34 s), while the other three answered in 1.4–1.7 s. The decomposition is also what disproved
  the claim I had written into the constants: a stopped container behind Docker Desktop's proxy does
  **not** accept and then go silent, it never accepts at all.
- **A pre-existing race found and not fixed: the token pool.** `auth.py` marks itself checked *before* the
  pool it read is in place, so a burst arriving inside that window is refused by the request gate with
  `503 the gateway cannot authenticate anyone right now`. Four threads through `TestClient` reproduce it;
  eight simultaneous requests against the live gateway did not, because the window is one file read.
  Reported rather than fixed — it belongs to the request gate, not to routing.

## The order of the list, and a correction

Nothing ranks the two cards: they have never been measured on the same frame, and the warm-prompt figure
is not a proxy — it read 0.34 s on the 3090 and then 0.909, 1.209 and **0.406 s across three runs on the
5090**, which is startup noise and lazy kernel compilation. A real request through the gateway answers in
0.9–1.0 s on the 5090 with the 5090 listed first. The order is therefore a policy choice, not a
correction, and the 5090 stays first because it is the machine that was verified end to end today.

## Open, and worth doing before the event

- **The resident figure understates the 5090.** The label and `/health` say `resident_gb 3.7` from a
  3812 MiB measurement on the 3090; on the 5090 the same container left ~7.7 GB used with ~1.1 GB of
  desktop, so roughly **6.5 GB**. Admission is unaffected at a 31 GB budget, but the number is what a
  human or the deploy script reads first, and `resident_gb` measurements belong per card.
- **Restart policy, resolved here.** The container runs `--restart unless-stopped` (what
  `gpu-backends.md` asks for: a backend must come back after a reboot with nobody logged in, and on
  Windows Docker Desktop needs a signed-in user on top of that), while `sam31_official/Dockerfile` said
  no restart policy anywhere and gave the refusal loop as the reason. The deploy side is right, so the
  comment now says which side decides and what the policy costs: a card that cannot admit becomes a
  container exiting every few seconds, visibly, instead of one that silently never returns.
- **The 5090's load prints `missing_keys=['backbone.vision_backbone.convs.3.conv_1x1.weight', …]`.** Same
  weights and same code as the 3090's verified measurement, so it is not a property of this machine —
  but nothing here records whether it is expected, and the first person to see it will think it is.
- **The build left 36 GB of images and 5.7 GB of cache on the 5090.** Nothing reclaims it automatically;
  the earlier session on that machine ended by freeing the same kind of space by hand.
