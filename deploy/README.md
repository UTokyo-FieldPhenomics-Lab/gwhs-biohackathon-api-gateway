# Deploy

Bootstrap for the gateway host: one EC2 instance (Debian 13) that serves the API, carries the
metadata index the gateway loads at startup, and publishes into the image plane. The image plane
itself — bucket, CloudFront, certificate, DNS — is built once per environment and is documented in
[`data-plane.md`](data-plane.md). Binding constraints are in
[`../.agents/rules/deploy.md`](../.agents/rules/deploy.md).

## Host configuration

| | |
|---|---|
| Instance | `t3.small` — 2 vCPU, 2 GiB, x86_64 |
| OS | Debian 13 "trixie" |
| Volume | one root volume, 40 GiB gp3 |
| Region and zone | the region is environment configuration; the dev host and the data bucket share one (`eu-central-1`), and no zone is pinned, so a replacement instance may land in any of them |

One volume is enough, and there is a measured reason: the separate 50 GiB volume this host began
with held **3 MB** when it was retired. It had been sized for a publisher's parquet cache, which is
now fetched onto this host's root volume by `biohack-datasets-fetch` and is **1.88 GB** for all
three datasets — rebuilt from the pinned revision, so it is not state to preserve
(`../.agents/rules/datasets.md`). A publish then streams each object straight to S3 and stages
nothing, so the 50 GiB volume has no job left. The checkout is under a megabyte and the virtualenvs
add a few hundred, so the root volume carries everything with room to spare.

Two CPUs is plenty: the gateway holds one small index in memory and answers from it, so its work
is network-bound rather than CPU-bound. The volume is encrypted; the account default key is
enough, since a customer-managed one buys nothing here.

Replacing the instance is an ordinary event, because nothing on it is the only copy of anything:
the code comes from git, the index from a publish, the certificates from ACME.

## Bring a host up

Add an inbound TCP 22 rule scoped to your own address, then log in:

```bash
curl -s https://checkip.amazonaws.com    # your address, for the /32 rule
ssh -i ~/.ssh/<key>.pem admin@<host>     # Debian images use `admin`, not `ec2-user`
```

The private key is the only credential. Cloud images give the default user no password, and
sshd refuses both password and keyboard-interactive authentication, so there is nothing else to
know. It must be `chmod 600`: at 0644 ssh ignores it and reports only `UNPROTECTED PRIVATE KEY
FILE`, which reads like a wrong key rather than a wrong permission.

## Bind the API domain

`api.<domain>` must point at the host before TLS can be issued for it, and the host must be given
an Elastic IP first — a stop/start otherwise changes the address and the host is meant to be stopped
between events.

1. Allocate and associate an Elastic IP, then confirm what the host itself sees:

   ```bash
   curl -s http://169.254.169.254/latest/meta-data/public-ipv4   # on the host: IMDS
   ```

2. At the registrar (the zone is `dnsv.jp`, not Route 53), create `api` as an `A` record to that
   address. TTL 3600.
3. **Check resolution before starting anything that uses a certificate.** ACME validates by
   resolving the name, and a resolver still holding the previous address fails the challenge and
   spends one of Let's Encrypt's five hourly attempts:

   ```bash
   curl -s 'https://dns.google/resolve?name=api.<domain>&type=A'
   ```

4. Start the reverse proxy (Caddy or nginx) on the host; TLS terminates there, not at CloudFront.
   The `data.<domain>` record is a `CNAME` to the distribution instead, and belongs to the image
   plane: see [`data-plane.md`](data-plane.md).

## Prepare the environment

Installs git and uv — Debian 13 ships no uv package, so it comes from the official installer —
and clones this repository into `/opt/gwhs-biohackathon-api-gateway`, owned by the user you sudo
from:

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/prepare-host.sh | sudo bash
```

Then, from the checkout, `cd gateway && uv sync`. The dataset cache is fetched on the same host,
because it is where a publish runs: `cd datasets && uv sync && uv run biohack-datasets-fetch`
(1.88 GB, rebuilt from the pinned revision, disposable — [`data-plane.md`](data-plane.md) explains
where those bytes go).

## Optional: put the host on the tailnet

`install-tailscale.sh` installs Tailscale and joins the tailnet; it is idempotent. With no
pre-authorization key it prints a URL and waits while you approve the machine in a browser.

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/install-tailscale.sh | sudo bash
```

The host is then reachable over the tailnet — `ssh -i ~/.ssh/<key>.pem admin@100.x.y.z`, same
key as before — so the port 22 rule can go away. In the Tailscale admin console:

1. **Disable key expiry** for the node. Expiry runs on the clock, not on activity, so a host
   stopped between events would otherwise drop off the tailnet exactly when it is needed.
2. **Tag the node and narrow the access policy.** The tailnet carries other owners' devices, and
   the gateway's internal surface inherits that audience — its token has to be a second lock.
