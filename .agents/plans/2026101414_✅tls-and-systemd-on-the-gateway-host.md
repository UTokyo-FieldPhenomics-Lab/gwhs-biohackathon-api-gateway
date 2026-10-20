# TLS in front of `api.*`, and the gateway as a systemd unit

Status: done — implemented and verified on the host on 2026-10-14 (see
[`../references/2026101415_caddy-and-systemd-on-the-host.md`](../references/2026101415_caddy-and-systemd-on-the-host.md)).
The files are in the working tree; nothing is committed yet.
Owner files: `deploy/Caddyfile`, `deploy/systemd/biohack-gateway.service`,
`deploy/setup-gateway.sh`, `deploy/README.md` §1.

## Why now

TLS and the service manager depend on nothing that is still missing:

| Needs | State |
|---|---|
| `api.global-wheat.com` resolving to the host's Elastic IP | done — the A record matches it |
| TCP 80/443 accepted from outside | done — open all along; the timeout I measured came from one workstation's own network |
| Something listening on `127.0.0.1:8000` | the skeleton app qualified; a real index is not needed |
| A checkout, a venv, `uv` | done by `prepare-host.sh` |

Not needed at all: the `data.*` base URL, the metadata index, the publisher. Those are the
gateway's own configuration, and none of them touches ACME, the security group, or systemd.
So "wait for the publisher" would only postpone the four environment risks this exercises —
reachability, DNS freshness, ACME issuance, and the unit's user/venv/PATH — to the week of the
event.

Certificate issuance is also independent of the upstream: Caddy obtains the certificate whether
or not the gateway answers, and answers `502` until it does. TLS can therefore be verified
before the application is finished.

## Decisions

- **Caddy, not nginx.** Automatic ACME issuance and renewal is the whole job; Caddy is the
  smaller configuration and the smaller set of moving parts (no renewal timer, no hook that
  reloads the proxy).
- **The domain is written out, not an environment variable.** The official Debian `caddy.service`
  loads no environment file (`ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile`,
  verified against the packaged unit), so `{$API_DOMAIN}` would need a systemd override for a
  value that is public and appears in this repository's prose and examples anyway. Two literals
  are accepted instead — one in `deploy/Caddyfile`, one in the script — and the script's closing
  smoke test makes disagreement between them fail loudly rather than silently.
- **The script aborts when the name does not resolve to this host.** ACME failures are the scarce
  resource (five per hour per account and hostname), and starting the proxy early is exactly how
  one is spent. No override flag: the only reason to bypass this check is one that costs a
  validation attempt.
- **The script cannot verify that 80/443 are open**, because the security group is a rule on the
  host's perimeter: from inside, the host sees only its own success. The script prints the
  external check to run and proceeds; the README makes it a step with its own command.
- **The host's private state is created, the application's configuration is not.** `env` (the
  unit's `EnvironmentFile=`) and the directory that holds it exist now, because the unit names the
  file and a missing one must fail the start rather than let the service run with defaults. Which
  variables go in `env` is the authentication work's to decide.
- **No key-pool tooling ships.** The pool may be a file an operator puts in the state directory or
  state the gateway maintains through its own endpoints, and a script that materializes a file
  presumes the first and fixes a filename and format before either is decided. The constraint that
  survives is the one in the rules: whatever lands there has to stay re-derivable or exportable,
  because the host is never the only copy of anything.
- **No systemd hardening beyond `User=` and `Restart=`.** `ProtectHome=` and
  `ProtectSystem=strict` would deny the state directory under `$HOME`, so adding them now would only
  guarantee a revert.
- **One worker.** The authentication and concurrency gates keep their counters in process
  memory; a second worker would enforce them per worker instead of globally.

## Steps

1. `deploy/Caddyfile` — site address plus `reverse_proxy 127.0.0.1:8000`.
2. `deploy/systemd/biohack-gateway.service` — `User=` and `EnvironmentFile=` rewritten at install
   time to the checkout's owner and their state directory, `ExecStart` the venv's `uvicorn` on
   loopback, `Restart=on-failure`.
3. `deploy/setup-gateway.sh`, idempotent, in this order:
   1. preflight — the checkout and `uv` exist; the authoritative `A` record for the name equals
      the host's own egress address;
   2. print the external 80/443 check (the one thing the host cannot see);
   3. install Caddy from its apt repository;
   4. copy `deploy/Caddyfile` to `/etc/caddy/Caddyfile`, then `systemctl reload-or-restart caddy`
      (a graceful reload when it is already running);
   5. `uv sync --frozen` in `gateway/`;
   6. create `~/.config/biohack-gateway/` and `env` when they are missing, then install the unit
      with `User=` and `EnvironmentFile=` rewritten, `daemon-reload`, `enable`, `restart` —
      `restart` rather than `enable --now`, so a second run after a `git pull` that changed the
      unit actually takes effect;
   7. smoke: both units active, and `https://api.global-wheat.com/` answering something other
      than `502` (one `curl`, retried over a bounded window, which also proves the certificate
      validates — the first request can arrive while ACME is still issuing).
4. `deploy/README.md` — open 80/443 and verify from outside, run the script, drop the manual
   `uv sync` from §1.2, and record where the settings live.

## Deliberately not

- `ss` port checks and `caddy validate`: both duplicate a failure Caddy's own start already reports.
- `cmp`-gated restarts: an unconditional graceful reload is simpler and converges.
- The `http://` redirect check: a certificate implies port 80 worked, which is where the challenge
  lives anyway.
- An assertion on a specific status code: it would tie the deploy script to the application's routes.
- The settings layer and `gateway/.env.example`: the authentication work's.
- The token pool's reading half. The pool file and the command that writes it exist
  (`biohack-gateway tokens list|add|remove`, `.agents/plans/2026101414_token-command-line.md`); what
  authenticates a request against it belongs to the authentication work.
- A `redeploy.sh`: re-running `setup-gateway.sh` already converges, at the cost of a redundant apt and
  Caddy pass.
- ACME notification address, a staging CA, and edge-level rate limiting.

## Verification

Run on the host on 2026-10-14, and recorded in
[`../references/2026101415_caddy-and-systemd-on-the-host.md`](../references/2026101415_caddy-and-systemd-on-the-host.md).

```bash
nc -vz api.global-wheat.com 443            # from a machine outside AWS; "refused" is the expected answer
sudo bash /opt/gwhs-biohackathon-api-gateway/deploy/setup-gateway.sh
curl -sI https://api.global-wheat.com/     # certificate validates; any status but 502
journalctl -u caddy -n 30                  # "certificate obtained successfully"
systemctl cat biohack-gateway              # User= matches the checkout's owner
stat -c '%a %U %n' ~/.config/biohack-gateway ~/.config/biohack-gateway/env   # 700/600, the service user
```

Re-running the script must converge: apt reports nothing to change, the certificate is reused
from `/var/lib/caddy` (Caddy renews when it is within 30 days of expiry, so a stop/start or a
restart does not spend a validation), and the service is restarted with the same unit. A value
added to `env` by hand survives a re-run, which is the point of creating the file only when it is
missing.

A terminated instance loses the certificate with the volume and reissues on the next run; that
is the reason certificates are allowed to come from ACME rather than being preserved.

Two things the first real run taught, both fixed or documented rather than remembered:

- **The smoke test failed on the second run until it retried `502` too.** `systemctl restart`
  returns when the process has been executed, not when it listens, so the first request of a re-run
  meets Caddy with a closed upstream. Retrying only the TLS handshake covered the first run and
  missed the second, which is precisely the case a re-run is for.
- **`timed out` from one machine is not evidence about the security group.** The group had 80 and
  443 open from `0.0.0.0/0` all along; the workstation that reported timeouts also timed out on 22,
  which the group allowed for its address. `refused` is the answer that means "open, nothing
  listening yet".
