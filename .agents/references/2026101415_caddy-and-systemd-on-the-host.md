# Caddy, ACME, and systemd on the gateway host

2026-10-14. The first real run of `deploy/setup-gateway.sh`, on the host as it is today — Debian 13
trixie, `t3.small`, Elastic IP `gwhs-host`, reached over the tailnet because port 22 on the
Elastic IP is not reachable from this workstation. Constraints this run produced are in
[`../rules/deploy.md`](../rules/deploy.md); the plan it verifies is in
[`../plans/2026101414_✅tls-and-systemd-on-the-gateway-host.md`](../plans/2026101414_✅tls-and-systemd-on-the-gateway-host.md).

## What the host looked like first

Read-only checks before anything ran, all by hand over SSH:

| Check | Result |
|---|---|
| Caddy, `/etc/caddy`, the unit, `~/.config/biohack-gateway` | none — nothing had been installed |
| Listening sockets | `sshd`, `tailscaled`; nothing on 80, 443, or 8000 |
| Checkout | `/opt/gwhs-biohackathon-api-gateway` at `b163801`, which is `origin/main` — local `main` was 10 commits ahead and unpushed |
| `uv`, `sudo -n`, `stat getent install mktemp seq` | all present; `sudo` passwordless |
| The script's own preflight, run by hand | `checkip` `= gwhs-host` `=` the authoritative `A` record → pass |
| `gnupg` | **absent** — the Debian image does not ship `gpg`, so installing it was necessary and not paranoia |

## The one precondition I got wrong

I measured `timeout` on 80 and 443 from this workstation and concluded the security group was closed,
and asked for the rules to be opened. The rules were already open, as the screenshot of the group
shows. The measurements that settled it:

```bash
# from four nodes abroad (check-host.net), before anything was installed:
#   {'error': 'Connection refused'}   → the packets arrived and the kernel refused them
# from this workstation:
#   Connection timed out (80, 443) — and on 22 as well, which the group allows for this address
# from the host itself:
#   Connection refused in 0 ms → AWS hairpins the host's own Elastic IP, so the script's
#   public-name smoke test is valid rather than a trap
```

So the block was on this workstation's path, not at the perimeter. The lesson and the reading rule are
now in [`../rules/deploy.md`](../rules/deploy.md) and `deploy/README.md` §1.5: `refused` means the group
is open and nothing is listening yet; `timeout` may be the checking network, and one vantage point is
not evidence.

## The run, in order

The three new files were copied into the host's checkout (`scp`), because they were untracked and a
`pull` cannot deliver them, and `sudo bash deploy/setup-gateway.sh` was run:

1. preflight passed; the note about the perimeter was printed.
2. `apt-get install gnupg` pulled 17 packages; the Cloudsmith key dearmored into
   `/usr/share/keyrings/caddy-stable-archive-keyring.gpg`; **Caddy 2.11.7** installed and enabled.
   The `debconf: unable to initialize frontend` lines are noise from a non-interactive apt, not a
   failure.
3. `/etc/caddy/Caddyfile` installed and `reload-or-restart` ran; Caddy logged
   `serving initial configuration` then `certificate obtained successfully` **6 s later**
   (`issuer: acme-v02.api.letsencrypt.org-directory`).
4. `uv sync --frozen` created `gateway/.venv` on the host as `admin` — 35 packages, 40 ms of install
   after 1.1 s of preparation.
5. `~/.config/biohack-gateway/` created 0700 with `env` 0600; the unit installed with `User=admin`
   and `EnvironmentFile=/home/admin/.config/biohack-gateway/env`, enabled and started.
6. The smoke test: the first request arrived before the certificate existed and died with
   `curl: (35) TLS connect error … tlsv1 alert internal error`, printed
   `waiting for the certificate…`, and on a later attempt answered **404 with a certificate the
   client accepted**. Exit 0.

Result: `https://api.global-wheat.com/` → `404`, `http://` → `308`, verifiable certificate for
`CN=api.global-wheat.com` (Let's Encrypt `YE1`, 2026-10-14 → 2027-01-12), `uvicorn` on
`127.0.0.1:8000` only, Caddy on `*:80` and `*:443`, both units `active` and `enabled`.

## What broke: the smoke test passed on the first run and failed on the second

The idempotency re-run exited **1** with `Caddy served, but the gateway did not answer`, and
`https://api.global-wheat.com/` answered `502` — while the unit was `active` and the app answered
`404` a few seconds later. Caddy's log named it exactly:

```
dial tcp 127.0.0.1:8000: connect: connection refused  (status 502, reverseproxy.statusError)
```

`systemctl restart` returns when the process has been **executed**, not when it listens, so the first
request of every re-run lands in that gap. The loop retried only on the TLS handshake failure (`000`),
so the first run was covered — the certificate did not exist yet, which bought the gateway time — and
the second was not: the handshake succeeded immediately, the 502 came back, and the script called it a
hard failure.

The fix, in `deploy/setup-gateway.sh`: retry while the status is `000` **or** `502`, and report
`502` only when it persists for the whole window. Verified by re-running: exit 0, one
`waiting for the certificate and the gateway to come up…` line, and `404` at the end.

Two things this says about the shape of the check, worth keeping:

- **A smoke test that asserts the whole chain has to tolerate every transient in the chain**, not just
  the one the author had in mind. The first-run pass was luck, not coverage.
- **The re-run is the test that matters.** It is the documented update path, and it exercises a state
  the first run cannot reach.

## Also observed

- **The certificate was reused, not reissued**: `obtaining certificate` appears once in the journal
  across both runs, so a re-run costs no ACME validation. Renewal is Caddy's, from
  `/var/lib/caddy`.
- **A hand-added line in `env` survived both re-runs**, which is the point of creating that file only
  when it is missing.
- **The surface is public and gets probed**: eight seconds after the certificate appeared, a request
  for `/robots.txt` arrived from `a scanner`. Nothing protects this host but the key that will
  guard the endpoints — which is the design, and a reminder that it is not a private host.
- `~/.config` was already present with mode 0775 and was left untouched; only
  `~/.config/biohack-gateway` was created, 0700.

## Left as it should be

The three `scp`-ed files were removed from the host's checkout afterwards, so the next `git pull` would
not be blocked by untracked files. The commits were then pushed, and the host pulled all 13 of them:

```bash
git -C /opt/gwhs-biohackathon-api-gateway pull --ff-only   # b163801 → dacb9db, fast-forward
sudo bash /opt/gwhs-biohackathon-api-gateway/deploy/setup-gateway.sh
```

That run is the update path the runbook describes, and it is the one that exercises the fixed smoke
test: exit 0, `waiting for the certificate and the gateway to come up…` printed once, `404` at the end,
and `obtaining certificate` still **once** in the journal — the certificate was reused, so a pull and a
re-run cost no ACME validation. `uv sync --frozen` rebuilt the package (the `[project.scripts]` entry
arrived with it), and the command works from the host's own virtualenv:
`gateway/.venv/bin/biohack-gateway tokens list` exits 0 on an empty pool, `add` prints a token, and the
pool lands where `--file` says. Five files' md5s match the working tree afterwards, and the checkout is
clean at `dacb9db`.

Not tested, and deliberately: a host reboot (the units are `enabled`, so boot-time start is wired but
unproven, and rebooting the host without asking is not mine to do).
