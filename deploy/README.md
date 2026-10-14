# Deploy

Everything that has to exist before a participant can fetch one image: **one EC2 host** that serves
the API, and **one private S3 bucket** that CloudFront serves images and masks from. Both halves are
done by hand, once, in the order written here, and there is one environment today — the host in
Frankfurt and the bucket beside it. Constraints behind every choice are in
[`../.agents/rules/deploy.md`](../.agents/rules/deploy.md).

## What exists

| | |
|---|---|
| Host | `t3.small` — 2 vCPU, 2 GiB, x86_64, Debian 13 "trixie", one 40 GiB gp3 root volume |
| Bucket | private, `eu-central-1`, Block Public Access on; CloudFront reads it through an origin access control and is its only reader |
| Distribution | `data.<domain>` — HTTPS only, `GET`/`HEAD`, long TTL, all edge locations |
| Certificate | ACM in **`us-east-1`**, because CloudFront requires it there whatever the origin's region is |
| Domains | `api.<domain>` → the host's Elastic IP, TLS terminated on the host; `data.<domain>` → the distribution |
| Region and zone | the region is environment configuration and the host and bucket share one (`eu-central-1`); no zone is pinned, so a replacement instance may land in any of them |

Three facts behind that table, each measured rather than assumed:

- **One volume is enough.** The separate 50 GiB volume this host began with held 3 MB when it was
  retired. The parquet cache it was sized for is 1.88 GB, is fetched onto the root volume and rebuilt
  from the pinned revision ([`../.agents/rules/datasets.md`](../.agents/rules/datasets.md)), and a
  publish streams each object straight to S3, so nothing large and nothing irreplaceable lives there.
- **Two CPUs are plenty.** The gateway holds one small index in memory and answers from it, so its
  work is network-bound rather than CPU-bound.
- **Replacing the instance is an ordinary event**, because nothing on it is the only copy of
  anything: the code comes from git, the index from a publish, the certificates from ACME.

---

# 1. The host

## 1.1 Launch it — AWS console

| Field (EC2 → Launch instance) | Value |
|---|---|
| Name | `biohackathon-api-server`, or your own |
| Application and OS Image | Debian 13 "trixie", x86_64 |
| Instance type | `t3.small` |
| Key pair | an existing one, or create one and keep the `.pem` |
| Network settings → Security group | inbound **TCP 22 from your own address only**; nothing else inbound. Your address: `curl -s https://checkip.amazonaws.com` |
| Configure storage | one gp3 root volume, 40 GiB, encrypted |

To change the rule on an existing host: EC2 → Security groups → select it → Edit inbound rules. The
port is never open to `0.0.0.0/0`.

The private key is the only credential. Cloud images give the default user no password, and sshd
refuses both password and keyboard-interactive authentication, so there is nothing else to know. It
must be `chmod 600`: at 0644 ssh ignores it and reports only `UNPROTECTED PRIVATE KEY FILE`, which
reads like a wrong key rather than a wrong permission. Connect with
`ssh -i ~/.ssh/<key>.pem admin@<host>` — Debian images use `admin`, not `ec2-user`.

## 1.2 Prepare it

Installs git and uv — Debian 13 ships no uv package, so it comes from the official installer — and
clones this repository into `/opt/gwhs-biohackathon-api-gateway`, owned by the user you sudo from:

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/prepare-host.sh | sudo bash
```

Then, from the checkout, `cd gateway && uv sync`. The dataset cache is fetched on this same host,
because it is where a publish runs: `cd datasets && uv sync && uv run biohack-datasets-fetch` —
1.88 GB into `datasets/.hf_cache/`, rebuilt from the pinned revision rather than preserved.

## 1.3 Optional: put it on the tailnet

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/install-tailscale.sh | sudo bash
```

It prints a URL to approve in a browser when there is no pre-authorization key, then the tailnet
address — `ssh -i ~/.ssh/gwhs-demo.pem admin@100.88.197.115`. Room machines use that address instead
of the port 22 rule. Then two things in the Tailscale admin console → Machines:

1. **Disable key expiry.** Expiry runs on the clock, not on activity, so a host stopped between events
   would otherwise drop off the tailnet exactly when it is needed.
2. **Tag the node and narrow the access policy.** The tailnet carries other owners' devices, and the
   gateway's internal surface inherits that audience — its token has to be a second lock.

If a room then cannot reach the host, the three checks and the failure this step caused on 2026-10-14
are in [`.agents/references/202610140230_tailnet-not-reachable-while-online.md`](../.agents/references/202610140230_tailnet-not-reachable-while-online.md);
the constraint behind them is in [`../.agents/rules/deploy.md`](../.agents/rules/deploy.md).

## 1.4 Bind the API domain

`api.<domain>` must resolve to the host before TLS can be issued for it, and the address must be
stable first: a stop/start otherwise changes the public IPv4, and this host is meant to be stopped
between events.

1. **AWS console** → EC2 → Elastic IPs → Allocate Elastic IP address → then Actions → Associate with
   the instance.
2. On the host, confirm the address it actually has — it must equal the Elastic IP in the console:
   ```bash
   curl -s https://checkip.amazonaws.com     # what AWS sees as this host's source address
   ```
3. At the registrar (the zone is served by `dnsv.jp`, not Route 53), create `api` as an **`A`**
   record to that address. TTL 3600.
4. **Check resolution before starting anything that uses a certificate.** ACME validates by
   resolving the name, and a resolver still holding the previous address fails the challenge and
   spends one of Let's Encrypt's five hourly attempts:
   ```bash
   curl -s 'https://dns.google/resolve?name=api.<domain>&type=A'
   ```
5. Start the reverse proxy (Caddy or nginx) on the host. TLS terminates there, and `api.*` never
   touches CloudFront — that is what `2.5` and `data.*` are for.

---

# 2. The dataset bucket (S3) and its CDN

What participants actually read: **one private bucket**, **one CloudFront distribution** in front of
it, **one certificate**, **one DNS record**. It is by hand on purpose — these are resources created
once and never deleted, so automating them buys nothing and a script that ran twice would rewrite a
live distribution.

Run it with your own admin session — there is no policy to create and nothing to detach. This
procedure only creates and verifies; it never deletes anything or touches Route 53.

## In this order

1. Create the bucket and leave it closed to the public.
2. Request the certificate in `us-east-1`, add its validation record at the registrar, wait for
   `Issued`.
3. Create the distribution: origin, origin access control, WAF left off.
4. Confirm the bucket policy CloudFront wrote.
5. Add `data.<domain>` and its certificate to the distribution, and point the CNAME at it.
6. Upload one object by hand and verify the whole path.
7. Record what you created.

Order matters: the certificate must be **Issued** before the distribution can use it, and the alias is
added to the distribution afterwards — this console only sets a domain inline when the zone is in
Route 53, and this one is at お名前.com.

## 2.1 Create the bucket

**AWS console** → S3 → Create bucket.

| Field | Value |
|---|---|
| Bucket name | globally unique, lowercase, no spaces |
| AWS Region | `eu-central-1` |
| Object Ownership | ACLs disabled (default) |
| Block all public access | **all four boxes checked** — this is the default, leave it |
| Bucket Versioning | Disabled |
| Default encryption | SSE-S3 (default) |

Neither versioning nor a KMS key buys anything here: a path is written once, so there is no previous
version to keep, and the objects are meant to be served to everyone through the distribution.

## 2.2 Request the certificate

**Switch the console's region selector (top right) to `us-east-1`** — this is the step people get
wrong, because everything else in this document is `eu-central-1`. Then Certificate Manager →
Request a certificate → Request a public certificate.

| Field | Value |
|---|---|
| Domain name | `data.<domain>` |
| Validation method | DNS |
| Key algorithm | RSA 2048 (default) |

Open the certificate → **Domains** and take the two strings from **Export to CSV**. Do **not** press
**Create records in Route 53** — it only exists for domains whose DNS is hosted in Route 53, 

`global-wheat` zone is at お名前.com managed by UTokyo. There: 「ネームサーバー/DNS」→「ドメインDNS設定（DNSレコード設定）」→
「DNSレコード設定を利用する」→ add a row, with **both strings trimmed**:

| Field | Enter | Why |
|---|---|---|
| ホスト名 | the CNAME name **minus** `.global-wheat.com.` — e.g. `_a1b2c3….data` | the field takes the label only and appends the domain itself; pasting ACM's name whole gives `…global-wheat.com.global-wheat.com` |
| TYPE / VALUE | `CNAME` / the CNAME value **minus** the trailing dot — `_x1y2….acm-validations.aws` | keep the leading `_`: it is part of the value |
| TTL / 状態 | `3600` / `有効` | |

Check the record is live **before** waiting, because ACM only polls and nothing in the console
triggers it:

```bash
curl -s 'https://dns.google/resolve?name=_a1b2c3….data.global-wheat.com&type=CNAME'
# must answer with the value you entered, ending in .acm-validations.aws.
```

`Pending validation` becomes `Issued` minutes later — up to 30 minutes is normal, a few hours at
worst; an empty answer above means waiting cannot help yet. Do not let it reach the 72-hour
`Validation timed out`, after which the certificate can only be requested again.

## 2.3 Create the distribution

**AWS console** → CloudFront → Create distribution → **Single website or app**.

| Field | Value |
|---|---|
| Distribution name | anything — it is only a tag |
| Billing | **Pay as you go** — a flat-rate plan attaches an AWS WAF web ACL and forbids removing it |
| Domain setup | **skip** — the zone is not in Route 53, so the domain goes on after creation (`2.5`) |
| Origin | pick the bucket from the dropdown — it fills in `<bucket>.s3.eu-central-1.amazonaws.com` |
| Grant CloudFront access to origin | **Yes** — the origin access control, and it writes the bucket policy (`2.4`) |
| Settings | **Use recommended origin settings** |
| Cache settings | **Use recommended cache settings tailored to serving S3 content** |
| Security protections | **Do not enable security protections** |

The wizard preconfigures everything else for an S3 origin, and every value is the one this document
wants — nothing to hunt for: viewer protocol **Redirect to HTTPS**, allowed methods **`GET, HEAD`**,
cache policy **`CachingOptimized`**, **Origin Shield off**, price class **all edge locations** (narrow
it later if every participant is in Europe; it changes which edges serve, not the object layout, so it
needs no republish). Check them on the distribution afterwards — Behaviors for the cache policy and
the methods, General for the rest.

The cache policy's own TTLs are 1 s / 1 day / 1 year, and an object's `Cache-Control` overrides them,
which is why the publisher sets one on every object.

## 2.4 Confirm the bucket policy

`Grant CloudFront access to origin: Yes` means CloudFront wrote it at creation. **AWS console** → S3 →
the bucket → Permissions → Bucket policy: one statement allowing `s3:GetObject` to the
`cloudfront.amazonaws.com` service principal, conditioned on `AWS:SourceArn` naming this distribution.

If it is absent, the distribution answers `403` for every key: an OAC signature is honoured by the
bucket policy, not by the distribution.

## 2.5 Add `data.<domain>` and point it here

This console sets a domain inline only for a Route 53 zone, so it happens on the distribution
afterwards: **AWS console** → CloudFront → the distribution → **Settings → Alternate domain names →
Add a domain** → `data.<domain>` → **TLS certificate**: the one from 2.2. Then:

1. Copy the **Distribution domain name** (`d1234abcd.cloudfront.net`), and if Status still reads
   `Deploying`, wait for `Deployed`.
2. At お名前.com, add a **`CNAME`**: ホスト名 `data` (the label only, as in 2.2), VALUE that domain name
   with no trailing dot, TTL 3600. The zone is not in Route 53, so it is a CNAME and not an alias.
3. Confirm it resolves before going on:
   ```bash
   curl -s 'https://dns.google/resolve?name=data.<domain>&type=CNAME'
   ```
4. Back in CloudFront: **Validate certificate**, then **Add domains**.

---

# 3. Verify

Upload one object and fetch it both ways. **AWS console** → S3 → the bucket → Upload → Add files →
pick any `.jpg` → expand **Properties** → set `Cache-Control` to
`public, max-age=31536000, immutable` and confirm `Content-Type` is `image/jpeg` (the console fills
it in from the extension; if it shows `binary/octet-stream`, correct it) → set the destination key to
`<dataset>/images/00000001.jpg` → Upload.

```bash
# the object is private: this must be 403
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://<bucket>.s3.eu-central-1.amazonaws.com/<dataset>/images/00000001.jpg"

# the distribution serves it, with the two headers
curl -sI "https://data.<domain>/<dataset>/images/00000001.jpg" |
  grep -iE '^(HTTP|cache-control|content-type|x-cache)'
```

Run the CloudFront request twice: the first answers `x-cache: Miss from cloudfront`, the second
`Hit`, which is the distribution serving from its cache rather than from the bucket. Those two
headers are not optional — every object the publisher writes must carry them, which is a rule rather
than a step here: [`../.agents/rules/datasets.md`](../.agents/rules/datasets.md).

Storage and requests are cents; the only cost that scales with use is CloudFront egress, and it stays
off the gateway.

# 4. Record what you created

None of these values belongs in the repository. Keep them where the host can read them — SSM
Parameter Store fits the rules already — and in whatever infrastructure notes you keep:

| Value | Needed by |
|---|---|
| Bucket name | the publisher |
| OAC id, distribution id | the next person to edit this distribution |
| Certificate ARN | renewals |
| Distribution domain name | the `CNAME` in 2.5 |
| `https://data.<domain>` | the gateway — **and only this**: the gateway never carries bytes and never touches the bucket, so the base URL is the whole interface it needs |
