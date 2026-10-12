# EC2 Gateway Host — Bring-up and Verified Facts

Date: 2026-10-12 23:11 JST
Topic: first EC2 host for the gateway (dev/demo environment), how it was reached, what was
verified on it, and the measurements that feed the December region decision.

## Objective

Stand up the gateway host so the 2026-10-20 demo can run, and collect the facts needed to
decide the December region. Region strategy agreed earlier: **build in Frankfurt now, decide
the December region together with the European compute leg on 10/20**.

## Verified facts (read from the running host)

| Item | Value | Note |
|---|---|---|
| OS / arch | Debian GNU/Linux 13 (trixie), x86_64 | x86 chosen deliberately to keep one architecture across local dev and cloud, and to avoid source-builds in the bootstrap script |
| Instance type | `t3.small` (2 vCPU / 2 GiB) | matches "2 vCPU class ≈ 18 USD/month" |
| Region / **AZ** | `eu-central-1c` | **the AZ is pinned**: the data volume lives here |
| IAM instance profile | attached (an EC2 role) | required by SSM; also the future S3 read path |
| SSM agent | `amazon-ssm-agent 3.3.5390.0`, active, `EC2 registration was successful` | **Debian images do not ship the agent** — it had to be installed by hand |
| Public IP | auto-assigned (ephemeral) | must become an Elastic IP before DNS points at it |
| Tailnet address | `100.88.197.115` (`biohackathon-api-server`) | |
| Volumes | root 8 GiB (ext4) + 50 GiB data volume | |
| Listening sockets | `sshd` on 0.0.0.0:22, `tailscaled` on the tailnet IPs, `systemd-resolved` | no gateway, no TLS yet |
| Installed tooling | `python3` only | `uv`, `git`, `caddy` all absent — bootstrap not yet run |

## Actions taken on the host

The 50 GiB volume was formatted and mounted, so state can live off the root volume:

```bash
sudo mkfs.ext4 -q -L biohack-data /dev/nvme1n1
# UUID=c098a889-4435-43b9-b2a4-ce0f85451a36
# /etc/fstab entry:
# UUID=<uuid> /srv/biohack ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2
```

`nofail` + `x-systemd.device-timeout=10` are deliberate: the volume is meant to be detached,
snapshotted and re-attached, and a missing device must never stop the host from booting.
The device was confirmed by **capacity** (53,687,091,200 bytes) before formatting, never by
device name, because Nitro renames block devices.

## Measured: Frankfurt ↔ Tokyo

| Measurement | Result |
|---|---|
| TCP RTT, Tokyo client → instance:80 | 256 / 289 / 373 ms |
| Tailscale DERP latency from the instance | `fra` 1.2 ms, `tok` 216.9 ms, `syd` no probe |
| `tailscale ping aliyun-tokyo-server` (from the instance) | `via DERP(tok) in 226 ms`, `direct connection not established` |

**Interpretation, and a correction to earlier reasoning.** For this pair the relayed path costs
almost nothing in latency: 226 ms relayed against a physical floor of roughly 220 ms. The
reason to want a direct path is **throughput and jitter** (DERP is a shared relay), not
latency. Do not spend further time on direct connectivity for interactive or small-payload
work; revisit it only when bulk transfer (model weights, image batches) is on the table.

Direct connectivity was not achieved, and the local side has been ruled out: an inbound UDP
41641 rule was added, and `tailscale ping` still reported `direct connection not established`.
The blocker is therefore at the Tokyo end — an Alibaba Cloud security group, or a university NAT
that is symmetric or refuses inbound UDP. `tailscale netcheck` on that peer would say which:
`UDP: false` or `MappingVariesByDestIP: true` means the direct path is hopeless.

That inbound UDP rule was then **removed**: with the latency penalty measured at roughly nothing,
it buys only throughput, and nothing on this host moves bulk data yet. Re-add it together with
bulk transfer (model weights, image batches) and confirm with `tailscale ping`, not by assumption.

## How the host is reached

`ssh -i <key> admin@100.88.197.115` works, as does the same over the public IP. Debian's
official images use the `admin` user, not `ec2-user`.

Important distinction: this is **plain OpenSSH reached over the Tailscale interface**, not
the Tailscale SSH feature. `tailscale debug prefs` reports `"RunSSH": false`, and the server
banner is `SSH-2.0-OpenSSH_10.0p2 Debian-7+deb13u1`. Enabling Tailscale SSH
(`tailscale set --ssh`) would remove the need for sshd and for key material, and is worth
doing before December — but **only after an ACL rule restricts it**, see below.

## Lessons

- **SSM needs two independent things, and the console diagnostics only name one.** Two hours
  were lost alternating between "no IAM role" and "no SSM agent". Debian images ship no
  agent, so the role alone can never be enough, and installing the agent requires exactly the
  access that SSM was supposed to provide. Both are on the host now, but SSM is **not** the path
  we rely on: an address-scoped inbound TCP 22 rule and the overlay are the two accepted routes,
  and this detour is why.
- **IAM users and user groups are irrelevant to an instance.** An instance assumes a **role**
  through an instance profile; there is no path from a user group to an EC2 resource.
- **CloudFront certificates must be requested in `us-east-1`**, whatever the origin region is.
  This applies to the `data.*` distribution only — `api.*` terminates TLS on the host with a
  Caddy/ACME certificate and never touches ACM.
- **The tailnet is shared.** It carries devices owned by other people (`labvpn@`,
  `labdevices@`, `guowei@`). Both the `/_internal/*` surface and any Tailscale SSH access
  inherit that audience, so tags and ACLs must narrow it; the internal token is a second lock,
  never the first.
- **Server nodes must have key expiry disabled** (admin console → Machines → Disable key
  expiry). Expiry is time-based, not activity-based: a host stopped for months drops off the
  tailnet exactly when it is needed as the out-of-band way back in.

## Open items

- The inbound TCP 22 rule is scoped to one address, which `deploy.md` now accepts as a standing
  option rather than a temporary exception. Removing it is a choice, not a cleanup.
- Allocate an Elastic IP; the auto-assigned public IPv4 changes on stop/start, and the host is
  meant to be stopped outside event windows.
- Inbound UDP 41641 was added and removed again; see the connectivity section above before
  re-adding it.
- Onboard the candidate GPU backends (`lab-pc`, `home-pc`) to the tailnet. They are not on it
  yet, and their reachability from Frankfurt is the measurement the December region decision
  actually needs.
- `deploy/` still owes the systemd unit, the Caddy configuration, and the volume-mount step
  (idempotent, checking the UUID rather than repeating the format). `install-tailscale.sh` and
  `prepare-host.sh` have landed.
