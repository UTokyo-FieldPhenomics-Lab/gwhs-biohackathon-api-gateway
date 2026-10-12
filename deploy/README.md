# Deploy

Bootstrap for the gateway host: one EC2 instance (Debian 13) that serves the API and, during a
release, runs the publisher against its own data volume. Binding constraints are in
[`../.agents/rules/deploy.md`](../.agents/rules/deploy.md).

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

## Prepare the environment

Installs git and uv — Debian 13 ships no uv package, so it comes from the official installer —
and clones this repository onto the data volume, owned by the user you sudo from:

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/main/deploy/prepare-host.sh | sudo bash
```

Then, from the checkout, `cd gateway && uv sync`.

## Optional: put the host on the tailnet

`install-tailscale.sh` installs Tailscale and joins the tailnet; it is idempotent. With no
pre-authorization key it prints a URL and waits while you approve the machine in a browser.

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/main/deploy/install-tailscale.sh | sudo bash
```

The host is then reachable over the tailnet — `ssh -i ~/.ssh/<key>.pem admin@100.x.y.z`, same
key as before — so the port 22 rule can go away. In the Tailscale admin console:

1. **Disable key expiry** for the node. Expiry runs on the clock, not on activity, so a host
   stopped between events would otherwise drop off the tailnet exactly when it is needed.
2. **Tag the node and narrow the access policy.** The tailnet carries other owners' devices, and
   the gateway's internal surface inherits that audience — its token has to be a second lock.
