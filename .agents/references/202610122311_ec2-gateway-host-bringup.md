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
| Region / AZ | `eu-central-1c` | nothing pins the zone: the second volume that did was retired the next day |
| IAM instance profile | attached (an EC2 role) | required by SSM; also the future S3 read path |
| SSM agent | `amazon-ssm-agent 3.3.5390.0`, active, `EC2 registration was successful` | **Debian images do not ship the agent** — it had to be installed by hand |
| Public IP | Elastic IP `gwhs-host`, associated | confirmed twice: IMDS `public-ipv4` and the instance's own view of its egress address |
| Domain | `global-wheat.com`, DNS at お名前.com (`01–04.dnsv.jp`) | `api.*` A → the EIP; `data.*` not created yet |
| Tailnet address | `gwhs-host` (`biohackathon-api-server`) | |
| Volume | one root volume, grown online 8 → 40 GiB | a second 50 GiB volume was tried, then retired |
| Checkout | `/opt/gwhs-biohackathon-api-gateway`, owned by `admin` | |
| Listening sockets | `sshd` on 0.0.0.0:22, `tailscaled` on the tailnet IPs, `systemd-resolved` | no gateway, no TLS yet |
| Installed tooling | `git`, `uv 0.13.0` (symlinked into `/usr/local/bin`) | `caddy` absent — bootstrap for it not yet run |

## The second volume, and why it was retired

The host was created with an extra 50 GiB EBS volume, formatted and mounted at `/srv/biohack`, on
the reasoning that state should live off the root volume. One day later it was measured and removed:

| | |
|---|---|
| Data volume usage at retirement | **3.0 MB of 49 GB** (the checkout was 984 KB; no virtualenv existed yet) |
| Where the checkout went | `/opt/gwhs-biohackathon-api-gateway`, on the root volume |
| Root volume before / after growing | 1.5 GB used of 7.7 GB → 1.5 GB used of 39 GB |

The volume had been sized for the publisher's parquet cache, which then moved to a scratch volume
discarded with the publish run — and publishing does not happen on this host at all, so nothing
remained that was either large or irreplaceable. Retiring it also unpinned the availability zone:
with one volume, a replacement instance may land in any zone of the region.

Two procedural notes worth keeping:

- **Growing the root on this image works online.** The GPT layout puts the ESP *before* the root
  partition (`p14` at sector 2048, `p15` at 8192, `p1` from 262144 to the end of the disk), so free
  space from an EBS growth lands immediately after `p1`: `growpart /dev/nvme0n1 1` then
  `/sbin/resize2fs /dev/nvme0n1p1`, no reboot and no partition surgery. `resize2fs` lives in
  `/sbin`, which is *not* on the PATH of a non-interactive SSH session — call it by absolute path.
- **Check before deleting a volume.** It was mounted read-only-where-possible and listed first;
  after the move it held only `lost+found`. The `umount` then had to precede a second mount of the
  same device, which the kernel refuses outright.

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

`ssh -i <key> admin@gwhs-host` works, as does the same over the public IP. Debian's
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
- `deploy/` still owes the systemd unit and the Caddy configuration. `install-tailscale.sh` and
  `prepare-host.sh` have landed; the volume-mount step is no longer needed, the second volume
  having been retired.
- **Do not point a certificate at a name whose cached address is stale.** `api.global-wheat.com`
  had just been set to the Elastic IP, but public resolvers still answered with the previous,
  now-released auto-assigned address for the length of its 3600 s TTL. ACME validates by
  resolving the name, so starting Caddy inside that window fails the challenge and spends one of
  Let's Encrypt's five failures per hour. Check resolution first:

  ```bash
  curl -s 'https://dns.google/resolve?name=api.global-wheat.com&type=A'
  ```

- A domain mix-up worth recording, because it cost a detour: `globalwheat.com` (no hyphen) is an
  unrelated parked domain on GoDaddy's nameservers, and checking *it* produced a confident and
  entirely wrong conclusion about our own DNS. The zone we own is `global-wheat.com`, served by
  `dnsv.jp`. Confirm the exact spelling before diagnosing a name.
