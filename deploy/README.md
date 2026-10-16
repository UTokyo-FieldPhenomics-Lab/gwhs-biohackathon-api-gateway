# Deploy

Everything that has to exist before a participant can fetch one image: **one EC2 host** that serves
the API, and **one private S3 bucket** that CloudFront serves images and masks from. Both halves are
done by hand, once, in the order written here, and there is one environment today — the host in
Frankfurt and the bucket beside it. Constraints behind every choice are in
[`../.agents/rules/deploy.md`](../.agents/rules/deploy.md).

§6 adds a third, independent piece: the curated documentation site, served by GitHub Pages rather than
by either of the two above, so it stays readable while the host is stopped.

## What exists

| | |
|---|---|
| Host | `t3.small` — 2 vCPU, 2 GiB, x86_64, Debian 13 "trixie", one 40 GiB gp3 root volume |
| Bucket | private, `eu-central-1`, Block Public Access on; CloudFront reads it through an origin access control and is its only reader |
| Distribution | `data.global-wheat.com` — HTTPS only, `GET`/`HEAD`, long TTL, all edge locations |
| Certificate | ACM in **`us-east-1`**, because CloudFront requires it there whatever the origin's region is |
| Domains | `api.global-wheat.com` → the host's Elastic IP, TLS terminated on the host by Caddy (§1.5); `data.global-wheat.com` → the distribution; `docs.global-wheat.com` → GitHub Pages (§6), which is not ours to run |
| Docs site | static, on GitHub Pages, published from an Actions artifact (§6) — no host, no bucket, and no certificate we manage |
| GPU backends | the compute plane's machines, which nothing here creates: the inventory, the fault domains the routers define, and the list format the gateway is configured from are in [`gpu-backends.md`](gpu-backends.md) |
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

## Getting in

The host we run, so a session starts without looking anything up. It is the *current* host: §1.1
launches a replacement and the addresses below change with it — `api.global-wheat.com` follows the
Elastic IP that §1.4 associates, and the tailnet address holds until the node is re-registered (§1.3).

```bash
ssh -i ~/.ssh/gwhs-demo.pem <user>@gwhs-host        # the tailnet: works from a room where port 22 does not
ssh -i ~/.ssh/gwhs-demo.pem admin@api.global-wheat.com  # public, if the security group allows your address
```

Inside, the checkout is `/opt/gwhs-biohackathon-api-gateway` — the path `prepare-host.sh` clones to, and
where §1.2's variable points.

## 1.1 Launch it — AWS console

| Field (EC2 → Launch instance) | Value |
|---|---|
| Name | `biohackathon-api-server`, or your own |
| Application and OS Image | Debian 13 "trixie", x86_64 |
| Instance type | `t3.small` |
| Key pair | an existing one, or create one and keep the `.pem` |
| Network settings → Security group | inbound **TCP 22 from your own address only**; 80 and 443 are added in §1.5, once the API has a name. Your address: `curl -s https://checkip.amazonaws.com` |
| Configure storage | one gp3 root volume, 40 GiB, encrypted |

To change the rule on an existing host: EC2 → Security groups → select it → Edit inbound rules. The
SSH port is never open to `0.0.0.0/0`; 80 and 443 are, because participants are everywhere — what
protects the API surface is the key, not the port.

The private key is the only credential. Cloud images give the default user no password, and sshd
refuses both password and keyboard-interactive authentication, so there is nothing else to know. It
must be `chmod 600`: at 0644 ssh ignores it and reports only `UNPROTECTED PRIVATE KEY FILE`, which
reads like a wrong key rather than a wrong permission. Connect with
`ssh -i ~/.ssh/<key>.pem admin@<address>` — the instance's public IPv4 from the console at this point,
and the Elastic IP from §1.4 on. Debian images use `admin`, not `ec2-user`, and the host we run now is
in [Getting in](#getting-in) above.

## 1.2 Prepare it

Installs git and uv — Debian 13 ships no uv package, so it comes from the official installer — and
clones this repository into `/opt/gwhs-biohackathon-api-gateway`, owned by the user you sudo from:

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/prepare-host.sh | sudo bash
```

To save retyping the path each session — note that the scripts set a `CHECKOUT` of their own for their
internal use, so exporting it here only affects your shell, not them:

```bash
export CHECKOUT=/opt/gwhs-biohackathon-api-gateway
cd "$CHECKOUT"   # go to github repo path
```

Then, from the checkout, fetch the dataset cache and rehearse a publish. Both run on this same
host, because this is where a publish reads from and writes to the bucket's region:

```bash
cd datasets && uv sync
uv run biohack-datasets fetch                                   # 1.88 GB into datasets/.hf_cache/
uv run biohack-datasets publish --dataset gwhs-temp --dry-run    # the plan: bucket, index path, rows
```

The cache is rebuilt from the pinned revision rather than preserved. A real publish also needs the
credential in §5. The gateway's own environment is synced, and installed behind TLS as a service,
in §1.5.

## 1.3 Optional: put it on the tailnet

```bash
curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/install-tailscale.sh | sudo bash
```

It prints a URL to approve in a browser when there is no pre-authorization key, then the tailnet
address — the one in [Getting in](#getting-in). Room machines use that address instead of the port 22
rule. Then two things in the Tailscale admin console → Machines:

1. **Disable key expiry.** Expiry runs on the clock, not on activity, so a host stopped between events
   would otherwise drop off the tailnet exactly when it is needed.
2. **Leave the access policy as it is, knowingly.** The tailnet carries other owners' devices under a
   single policy of everything-reaches-everything, so the gateway's internal surface inherits that
   audience. The fix would be one ACL file — and it is one file for the *whole* tailnet, so tightening
   it rewrites four other owners' policy and can break access they rely on. For the event we accept
   the exposure and let the per-service token be the only lock, which leaves a tailnet peer able to
   reach an inference port and bypass the gateway's concurrency gate, while exposing no key material.
   The invariant this deviates from is in [`../.agents/rules/project.md`](../.agents/rules/project.md);
   the reasoning, and the trigger to revisit, are in
   [`../.agents/plans/2026101609_gpu-compute-plane.md`](../.agents/plans/2026101609_gpu-compute-plane.md).

If a room then cannot reach the host, the two checks and the cause are in
[`.agents/references/2026101513_host-unreachable-from-the-utokyo-network.md`](../.agents/references/2026101513_host-unreachable-from-the-utokyo-network.md);
the constraint behind them is in [`../.agents/rules/deploy.md`](../.agents/rules/deploy.md).

**A backend machine needs no client of its own.** The node in that console per location is a router
advertising its own `/24`, so the machines behind it are reached by their own private addresses with
nothing installed on them — [`gpu-backends.md`](gpu-backends.md) holds that inventory, the fault domains the
routers define, and the list the gateway is configured from.

## 1.4 Bind the API domain

`api.global-wheat.com` must resolve to the host before TLS can be issued for it, and the address must
be stable first: a stop/start otherwise changes the public IPv4, and this host is meant to be stopped
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
   curl -s 'https://dns.google/resolve?name=api.global-wheat.com&type=A'
   ```
5. That is all the DNS there is; the proxy that terminates TLS for this name is §1.5. `api.*` never
   touches CloudFront — that is what §2.5 and `data.*` are for.

---

## 1.5 Start the API surface

TLS terminates on the host, so this is where `api.global-wheat.com` becomes real. Caddy obtains the
certificate over ACME and renews it by itself, and the gateway runs as a systemd unit behind it —
enabled at boot, because this host is stopped and started around events with nobody logged in to
start it by hand.

Ports first, if they are not open already. **AWS console** → EC2 → Security groups → the host's group →
Edit inbound rules → add **TCP 80** and **TCP 443** with source `0.0.0.0/0`. Neither can be scoped to an
address the way the SSH rule is: participants are everywhere, and what protects this surface is the API
key. Port 80 is not optional even though every client uses HTTPS — the ACME challenge and the redirect
to HTTPS both live there.

The host cannot verify its own perimeter, so check the ports from somewhere else first — and read the
answer carefully, because the two failures look alike and mean opposite things:

```bash
nc -vz api.global-wheat.com 443
nc -vz api.global-wheat.com 80
```

- **`Connection refused`** — the security group is open and nothing is listening yet. That is exactly
  what this step should report before the script runs, so it is a pass.
- **`timed out`** — something is dropping the packets, and it is not necessarily the group: a machine
  whose own network blocks that address reports the same thing. Confirm from a second network (any
  public TCP checker does it) before touching a rule. On 2026-10-14 a workstation timed out on 80 and
  443 *and* on 22, although the group allowed that address on 22; the perimeter had been open all
  along, four nodes abroad answered `refused`, and the certificate was issued on the first attempt.

Then, on the host:

```bash
sudo bash /opt/gwhs-biohackathon-api-gateway/deploy/setup-gateway.sh
```

It checks that `api.global-wheat.com` resolves to this host, installs Caddy from its own apt
repository, writes `/etc/caddy/Caddyfile`, syncs the gateway's environment, installs and starts
`biohack-gateway.service`, and finishes by fetching `https://api.global-wheat.com/health` — retrying
for up to a minute, because the certificate and the gateway's socket may both still be coming up the
first time it asks. `200` on that route means DNS, the security group, ACME, the proxy, the unit, and
the application are working together; any other answer fails the script, and the message names the
journal to read. It asks `/health` because that is the one route needing no key — so the script never
has to hold a participant credential — and because it is the application answering rather than Caddy.

Re-running it is the update path after a `git pull`: it rewrites both configuration files from the
checkout, so edit `deploy/Caddyfile` and `deploy/systemd/biohack-gateway.service` in the repository
and re-run, rather than editing anything under `/etc`.

### Where the gateway's settings and tokens live

The service's host state lives in `~/.config/biohack-gateway/` on the host, outside the checkout: the
directory is 0700 and owned by the user the service runs as, `env` is 0600, and systemd hands that file
to the process through `EnvironmentFile=`.

`setup-gateway.sh` creates both once — the file with a header and no values — and a re-run never
overwrites them. When a setting exists, it is added by hand and takes effect at process start:

```bash
ssh admin@<host>
#   printf 'VARIABLE=value\n' >> ~/.config/biohack-gateway/env     # it is 0600, owned by that user
sudo systemctl restart biohack-gateway
```

The gateway reads no setting from it today. Its index directory and image base URL are `create_app`
parameters whose defaults already hold this environment's values, so `env` stays empty and systemd
needs the file only to exist. A second environment moves those two by code rather than by an edit
here; the names — never values — would then be recorded in
[`../gateway/.env.example`](../gateway/.env.example).

The directory holds four things, and which of them the gateway can read is the point:

```
~/.config/biohack-gateway/          0700, owned by the user the service runs as
├── env                             systemd EnvironmentFile=; must exist; 0600
│                                   one setting, BIOHACK_BACKEND_TOKEN — the credential the gateway
│                                   presents to a GPU backend (below)
├── tokens.json                     participant token pool; 0600
├── models.json                     one entry per model, its backend URLs in the order they are
│                                   tried; 0600 — absent means the model surface serves no models
├── index/                          <dataset>.db per published dataset; read by the gateway
└── publish/                        0700; excluded from the service as a whole directory
    ├── env                         BIOHACK_BUCKET, AWS_REGION, BIOHACK_ACCESS_KEYS_CSV; 0600
    └── awsAccessKeys.csv           the console's access-key download; 0600
```

`setup-gateway.sh` creates the directory, `publish/`, and `env`, and nothing else: `tokens.json`
appears with the first issued token, `models.json` with the first backend written into it, `index/`
with the first publish, and `publish/` is filled in §5. Both of the first two are edited by hand, and
neither is in the repository; the model-to-backend list and the token it pairs with are documented in
[`gpu-backends.md`](gpu-backends.md), which is where the machines they name are.

**The one setting the gateway reads from its environment is the backend token.** It is the credential
it presents to a GPU backend, it has to match what that backend was started with, and it is unset by
default: a gateway without it answers every inference with `503` rather than letting a backend reply
`401`, because that reply would read to a participant as their own key being wrong. Rotating it means
changing both ends, which is one `EnvironmentFile=` and one container restart — there is no second
copy anywhere, and the value never enters the repository.

**What `index/` holds today:** `gwhd.db`, `gwfss.db`, and `gwhs-temp.db` — the first ten rows of each
dataset, published on 2026-10-15 so that all three answer while the full sets are still being prepared.
The two published datasets are the ten-row prefix of their release, which is what the documentation
site tells a participant; the full publish runs the same command without `--limit`, writes the same
three files, and the service picks them up on its next restart.
`InaccessiblePaths=` names the whole `publish/` directory rather than individual files, so a file added
there later is excluded by default — and it is created up front because systemd refuses a unit whose
excluded path does not exist.

The participant token pool is `tokens.json` in that same directory, and it is **the one thing in this
repository that is authoritative on the host** rather than kept outside it. Issuing a token from the
machine that checks it deletes a whole sync direction, and a token is re-issuable rather than
irreplaceable — but the price is real and it is worth stating: **terminating this instance voids every
participant's token**, because nothing else knows their keys. The copy away from the host is therefore
refreshed whenever the pool changes, and it is one command:

```bash
scp admin@<host>:~/.config/biohack-gateway/tokens.json ~/private/
```

The pool is edited by `biohack-gateway tokens …`, run in the gateway's own environment on the host. The
console script is not on `PATH` for the service user, so run it out of the checkout — and there is
nothing to restart afterwards: the gateway re-reads the file as soon as it changes, so an issued,
rotated or revoked token applies to the next request.

```bash
ssh admin@<host>
cd /opt/gwhs-biohackathon-api-gateway/gateway
uv run biohack-gateway tokens add --id team-07     # the token goes to stdout, once; notes go to stderr
```

`uv run biohack-gateway tokens --help` lists the rest. Issuing an id that already has a token fails,
which is the point — a working team is not rotated by accident. `--force` rotates a token while keeping
the team id, which is what a leaked credential needs; `--token` stores a value the team generated
itself; `list` prints team ids only and never tokens, which is what makes it safe to paste anywhere;
`remove --id` revokes. The file holds tokens verbatim, so a token that goes missing before it reaches
its team can be read back out of it. `--file` points the command at a copy instead of the default path,
which is how the same command runs against the copy taken above, on any machine:

```bash
uv run biohack-gateway tokens list --file ~/private/tokens.json
```

A team sends its token in the `X-API-Key` header. **Everything requires one except `/health` and the
documentation pages** (`/docs`, `/redoc`, `/openapi.json`), which carry no data and could not be
opened in a browser if the page that lists the endpoints needed a header itself. An unknown path is
refused before routing as well, so the open surface cannot be probed into existence:

```bash
curl -s                     https://api.global-wheat.com/health          # {"status":"ok","version":…}
curl -s                     https://api.global-wheat.com/                # 401 invalid_key
curl -s -H "X-API-Key: …"   https://api.global-wheat.com/                # 404 not_found: the token is good, the path is not
```

A missing or unknown token is `401` with `invalid_key`, and the presented value is never echoed back.
A pool that cannot be parsed is `503` with `unavailable` — the operator's problem rather than the
caller's — and only until it parses. While that is happening `/health` still answers, so a monitor sees
a live process with a broken pool rather than a dead host. An edit that breaks a pool the gateway was
already serving is not the same: the last pool that parsed keeps answering, and one line in
`journalctl -u biohack-gateway` names the file and the parse error.

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
5. Add `data.global-wheat.com` and its certificate to the distribution, and point the CNAME at it.
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
| Domain name | `data.global-wheat.com` |
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

## 2.5 Add `data.global-wheat.com` and point it here

This console sets a domain inline only for a Route 53 zone, so it happens on the distribution
afterwards: **AWS console** → CloudFront → the distribution → **Settings → Alternate domain names →
Add a domain** → `data.global-wheat.com` → **TLS certificate**: the one from 2.2. Then:

1. Copy the **Distribution domain name** (`d1234abcd.cloudfront.net`), and if Status still reads
   `Deploying`, wait for `Deployed`.
2. At お名前.com, add a **`CNAME`**: ホスト名 `data` (the label only, as in 2.2), VALUE that domain name
   with no trailing dot, TTL 3600. The zone is not in Route 53, so it is a CNAME and not an alias.
3. Confirm it resolves before going on:
   ```bash
   curl -s 'https://dns.google/resolve?name=data.global-wheat.com&type=CNAME'
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
curl -sI "https://data.global-wheat.com/<dataset>/images/00000001.jpg" |
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
| Bucket name | the publisher, and `BIOHACK_BUCKET` in its `publish/env` (§5) |
| OAC id, distribution id | the next person to edit this distribution |
| Certificate ARN | renewals |
| Distribution domain name | the `CNAME` in 2.5 |
| `https://data.global-wheat.com` | the base URL the gateway addresses images from — **and only this**: the gateway never carries bytes and never touches the bucket, so the base URL is the whole interface it needs |
| Index directory (`~/.config/biohack-gateway/index`) | written by a publish, read by the gateway at startup |
| Access-key CSV (`publish/awsAccessKeys.csv`) | the publisher, named by `BIOHACK_ACCESS_KEYS_CSV` in `publish/env` (§5) |

---

# 5. The publish credential

Publishing writes into the bucket and **the gateway never does** — it does not know the bucket name
and has no S3 client. So this credential belongs to the publisher alone, is scoped to the dataset
prefixes rather than to the bucket, and lives in a file the gateway's service cannot read.

Run this with your own admin session: five minutes, and it never deletes anything.

## 5.1 Create the policy

**AWS console** → IAM → Policies → Create policy → JSON.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublishDatasetPrefixes",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": [
        "arn:aws:s3:::<bucket>/gwhd/*",
        "arn:aws:s3:::<bucket>/gwfss/*",
        "arn:aws:s3:::<bucket>/gwhs/*",
        "arn:aws:s3:::<bucket>/gwhs-temp/*"
      ]
    }
  ]
}
```

- `<bucket>` is the name recorded in §4. Nothing else is substituted.
- **`s3:GetObject` is the `HEAD`.** There is no separate `s3:HeadObject` action, and the publisher's
  idempotency check *is* a `HEAD` — that is why the read action is granted at all. Because this policy
  grants no `s3:ListBucket`, S3 answers `403` for a key that does not exist rather than `404`; the
  publisher settles a denied `HEAD` with a conditional create (`If-None-Match: *`), which succeeds
  only on a free key, so no `ListBucket` is needed.
- **No `s3:DeleteObject`**: the publisher never deletes. No `s3:ListBucket` either: nothing enumerates
  the bucket on a routine run, and the conditional create above is what makes that absence safe.
- **One ARN per dataset prefix, and that list is the authorisation point.** A dataset id that is not
  listed here cannot be published into, so a new release — `gwhs` when its annotation lands — costs one
  deliberate edit rather than arriving by accident.

## 5.2 Create the user

**AWS console** → IAM → Users → Create user → name it `biohack-publish`, **no console access** →
Attach policies directly → the policy from 5.1.

## 5.3 Create its access key

**AWS console** → that user → Security credentials → Create access key → **Application running outside
AWS** → Other. The secret is displayed once; it is never retrievable again.

The console also offers **Download .csv**, and that download **is** the credential record — §5.4
places it as-is. Its columns are exactly `Access key ID` and `Secret access key`, with one data row
(the first column's header carries a byte-order mark). `.gitignore` names the console's download
(`<user>_accessKeys.csv`) as a backstop, not a licence: an unencrypted secret is never committed and
never copied into the checkout.

## 5.4 Put the key where the publisher looks

```bash
ssh admin@<host>
install -d -m 700 ~/.config/biohack-gateway/publish
```

Then place the console's download there, under the name §5.5 points at:

```bash
# on the workstation, where the browser put it
scp ~/Downloads/biohack-publish_accessKeys.csv admin@<host>:/tmp/awsAccessKeys.csv

# on the host
ssh admin@<host>
install -m 600 /tmp/awsAccessKeys.csv ~/.config/biohack-gateway/publish/awsAccessKeys.csv
rm /tmp/awsAccessKeys.csv
```

That CSV **is** the credential record — nothing transcribes it, and there is no `credentials` file to
keep in step with it:

- **Its columns are the two values.** `Access key ID` and `Secret access key`, one data row. The
  publisher reads exactly that shape (`biohack_datasets.credentials`); the header's byte-order mark
  and a browser's CRLF endings are handled there.
- **AWS SDKs cannot read it themselves.** A shared-credentials file is INI, so pointing
  `AWS_SHARED_CREDENTIALS_FILE` at a CSV fails; the publisher reads the two values and hands them to
  its boto3 session directly instead.
- **The secret never enters the environment.** The pair goes from the file into the session object,
  so `/proc/<pid>/environ`, logs, crash dumps and child processes never see it.
- **Why not the default `~/.aws/credentials`.** Everything the publisher owns goes under `publish/`,
  which the unit excludes as a whole directory (§1.5), so the key is not merely unreferenced by the
  gateway — it is unreadable by it. In the standard location the service *could* read it: the two run
  as the same user and 0600 does not separate them, so it would be safe by convention only.
- **Never commit it.** `.gitignore` names the console's download as a backstop, not a licence. Mode
  0600, outside the checkout, not backed up anywhere else; a replacement instance means a new key
  (§5.6).

## 5.5 Point the publisher at it

```bash
install -m 600 /dev/null ~/.config/biohack-gateway/publish/env
```

Then edit `~/.config/biohack-gateway/publish/env`:

```bash
BIOHACK_BUCKET=s3://<bucket>
AWS_REGION=eu-central-1
BIOHACK_ACCESS_KEYS_CSV=/home/admin/.config/biohack-gateway/publish/awsAccessKeys.csv
```

- **Three names, no secret.** The bucket, the bucket's region, and the path to the CSV. The key
  itself stays in the CSV, because anything in the environment shows up in `/proc/<pid>/environ`, in
  crash dumps and in child processes.
- **`BIOHACK_ACCESS_KEYS_CSV` is how the publisher finds the key.** Unset, boto3's standard chain
  applies instead — which is what a laptop uses, and why the variable is optional.
- **`AWS_REGION` must be the bucket's region** (`eu-central-1`), so no publish pays for a
  cross-region redirect.
- **It is a separate file from the gateway's `env`.** That one is handed to the service through
  systemd's `EnvironmentFile=`, so putting these in it would give the gateway process a credential
  that can write the bucket — the one thing it must never hold.
- **The publisher sources it; nothing else does.** The unit excludes the whole `publish/` directory,
  so the service can read neither this file nor the CSV beside it. Prefer a one-word function over an
  unconditional `~/.bashrc` export:

  ```bash
  # ~/.bashrc on the host
  gwpublish() { set -a; . ~/.config/biohack-gateway/publish/env; set +a; cd "$CHECKOUT/datasets"; }
  ```

- The variable **names** are recorded, and only the names, in the committed
  [`datasets/.env.example`](../datasets/.env.example).

## 5.6 Verify it, and rotate it

The publisher itself is the check that matters:

```bash
gwpublish                                              # sources publish/env, enters the checkout
uv run biohack-datasets publish --dataset gwhs-temp --dry-run   # the plan; touches no credential
uv run biohack-datasets publish --dataset gwhs-temp --bucket s3://<bucket>
```

A credential error names the file, never a value. For a read-only spot-check with the AWS CLI —
which, unlike the publisher, cannot read the CSV — export the pair for that one command:

```bash
gwpublish
export AWS_ACCESS_KEY_ID=$(awk -F, 'NR==2 { print $1 }' $BIOHACK_ACCESS_KEYS_CSV | tr -d '\r')
export AWS_SECRET_ACCESS_KEY=$(awk -F, 'NR==2 { print $2 }' $BIOHACK_ACCESS_KEYS_CSV | tr -d '\r')
aws sts get-caller-identity                       # the key is valid, and the right account
aws s3api head-object --bucket <bucket> --key <any key that exists>
```

`get-caller-identity` proves the key is valid. The `head-object` proves the read half of the policy
against a real object, if one exists yet — on a fresh bucket there is none, and skipping it proves
nothing either way. The export is for the CLI alone; the publisher never needs it.

**The write half is proven by the first publish**, which fails with `AccessDenied` naming the missing
action if the policy is wrong. That is deliberate: proving it by hand means writing a throwaway
object into a dataset prefix, and the publisher has no `DeleteObject` to remove it with — the cleanup
would need your own credentials.

**Rotation** is create-then-replace-then-delete: create a second access key for the same user,
replace the CSV at `publish/awsAccessKeys.csv`, confirm with `get-caller-identity`, then delete the
first key in the console. Replacing the instance means the same three steps on the new host.

---

# 6. The documentation site

The curated documentation is a static site: Zensical builds it, GitHub Pages serves it, and nobody runs
a server for it. It is independent of §1 and §2 by construction, and `api.global-wheat.com/docs`
remains FastAPI's own Swagger UI — the site links to it, and it links back. The record below and the
Pages settings are made by hand, once, like the bucket and the distribution.

| | |
|---|---|
| Site | `https://docs.global-wheat.com` — static, built from `zensical.toml` and `docs/` by [`../.github/workflows/docs.yml`](../.github/workflows/docs.yml) |
| Publishing source | GitHub Actions: an artifact is uploaded and deployed, so Jekyll is not involved and no `CNAME` file is committed |
| DNS record | `CNAME` `docs` → `utokyo-fieldphenomics-lab.github.io` — no repository name — TTL 3600, at お名前.com |
| Certificate | issued by GitHub for the custom domain; nothing here requests or renews it, and we cannot reissue it |
| Publication | any push to `main` touching `docs/**` or `zensical.toml`; a pull request builds with `--strict` and deploys nothing |

Three facts behind that:

- **It must not share the image plane.** Objects under `data.*` are write-once and are never
  invalidated, while this site is rewritten on every push: §2's long TTL is right for one and wrong
  for the other.
- **It must not share the API's failure domain.** The gateway host is on demand, and one measured
  network cannot reach it at all
  ([`../.agents/references/2026101513_host-unreachable-from-the-utokyo-network.md`](../.agents/references/2026101513_host-unreachable-from-the-utokyo-network.md))
  — not a property a reader should inherit.
- **It is public.** The repository is public, so Pages needs no plan change and the site is
  world-readable: nothing secret and nothing participant-specific may enter `docs/`.

## 6.1 Turn it on

Five steps, **in this order**: three in the console, one in the `お名前.com` panel, one to finish. The
custom domain is saved in the repository before the DNS record exists; the other order lets someone
else publish a site on the subdomain. This needs repository admin rights, and the DNS entry needs
whoever holds the panel.

1. **Organization → Settings → Pages** — confirm publishing is permitted for the organization.
2. **Repository → Settings → Pages → Build and deployment → Source: GitHub Actions.** A branch source
   would run Jekyll over the sources, which is not what the workflow expects.
3. Same page, **Custom domain**: `docs.global-wheat.com` → **Save**.
4. At お名前.com, in the same panel as §2.2, add **one** record — a `CNAME`, not the four `A` records:
   - **`CNAME`**: ホスト名 `docs` (the label only — the panel appends `.global-wheat.com`, and pasting
     the name whole gives `docs.global-wheat.com.global-wheat.com`), VALUE
     `utokyo-fieldphenomics-lab.github.io` with no trailing dot, TTL 3600.
   - The value is not a guess: `www.global-wheat.com` in this very zone already points at it, for the
     `www.global-wheat.com` repository's own site. `185.199.108–111.153` are for an apex domain and
     are not used here.
   - That `www` record is a different site's and does not interact with this one: a custom domain is
     attached per repository and `docs` and `www` are different names. `api` in this zone is the
     host's Elastic IP (§1.4), `data` the distribution (§2.5), and the `_…data` record is the ACM
     validation for the certificate in §2.2 — this adds a fourth, unrelated one.

   ```bash
   curl -s 'https://dns.google/resolve?name=docs.global-wheat.com&type=CNAME'
   ```

5. Once it resolves and the certificate has been issued — up to 24 hours, and the checkbox is absent
   until then — back in **Settings → Pages**, select **Enforce HTTPS**, then run §6.3.

Nothing needs to be published for those steps to be saved: the name starts answering once §6.2's
workflow has deployed once, and the certificate follows the name rather than the content.

### Optional: verify the domain

Verifying a domain protects it, and its immediate subdomains, from being attached to someone else's
Pages site. Two reasons this is optional here:

- **It may already be done.** If `global-wheat.com` appears under **Organization → Settings → Pages →
  Verified domains**, its subdomains are covered and there is nothing to add.
- **Its only job starts when the site ends.** While this site is live and the `CNAME` is in place,
  nothing needs the verification; it matters on the day the site is disabled and the record is
  forgotten, because a `CNAME` still pointing at GitHub Pages with no site behind it is a takeover
  path.

If you skip it, §6.4's "remove the `docs` `CNAME`" stops being tidiness and becomes the thing that
closes that path. If you do it, add the `_github-pages-challenge-utokyo-fieldphenomics-lab…` name and
value GitHub shows under *Add a domain* as a `TXT` row in the same panel visit, and keep it there
afterwards.

## 6.2 Build and publish

Nothing here runs on a host, and nothing here is a secret: the workflow holds the deploy credential as
its own short-lived token. It is also entirely repository-local — the organization can publish any
number of Pages sites, each has its own settings, and `<organization>.github.io` is only the `CNAME`
target they share. One custom domain serves one site at a time, which is why this is a subdomain of
`global-wheat.com` rather than a path under the lab's existing site. Locally:

```bash
uv sync --locked --project docs
uv run --project docs zensical build --strict     # exit 0; writes site/, which git ignores
uv run --project docs zensical serve              # http://localhost:8000
```

`zensical.toml` sits at the repository root and its `docs_dir` and `site_dir` are resolved relative
to that file, which is what makes `docs/content/` the content root and `site/` the output. The content
root holds site material and nothing else — not the subproject's virtualenv, which suppresses the
build's assets — and the runbook is this file, so the site never grows a second README to keep honest.

## 6.3 Verify

```bash
curl -sI https://docs.global-wheat.com/ | head -3
curl -sI http://docs.global-wheat.com/ | head -3
curl -sI https://utokyo-fieldphenomics-lab.github.io/gwhs-biohackathon-api-gateway/ | head -3
curl -s https://docs.global-wheat.com/ | grep -o '<title>[^<]*</title>'

# the certificate must be ours, not GitHub's wildcard
echo | openssl s_client -connect docs.global-wheat.com:443 -servername docs.global-wheat.com 2>/dev/null |
  openssl x509 -noout -subject -ext subjectAltName
```

Expected: `200` with `server: GitHub.com`; a `301` to the `https` URL; a `301` to
`docs.global-wheat.com`; the site's title; and a certificate whose subject is
`docs.global-wheat.com`.

Two readings that look like failures and are not, both measured on this organization on 2026-10-15:

- **The `github.io` path may redirect somewhere else first.** This organization's user site has its own
  custom domain, so `utokyo-fieldphenomics-lab.github.io/<repo>/` 301s host-wide to
  `lab.fieldphenomics.com/<repo>/` until that repository has a custom domain of its own. Once
  `docs.global-wheat.com` is saved in step 3, the 301 must point at it instead — the
  `www.global-wheat.com` repository shows exactly that, its path redirecting to its own domain.
  Re-run this check after the first deploy, not before.
- **The certificate is a separate event from the record.** `https://www.global-wheat.com/` still
  answers GitHub's wildcard `CN=*.github.io`, whose `subjectAltName` does not include that name, so
  every HTTPS client fails while `http://` returns `200`. That site's custom domain was added and the
  certificate never issued, and it is the state this check exists to catch: a resolving `CNAME` and a
  pending certificate look identical from the DNS side. If the same certificate appears here,
  re-save the custom domain in **Settings → Pages** and wait; `Enforce HTTPS` stays unavailable until
  GitHub has issued one.

Then push one visible change to a page and watch it appear — that, and not the name resolving, is what
proves the workflow is wired to the domain. In a browser the padlock must be GitHub's certificate for
`docs.global-wheat.com`.

## 6.4 When the event is over

The site is disposable by rule. **Settings → Pages → Unpublish** takes it down. Then remove the `docs`
`CNAME` at お名前.com: that is what closes the takeover path. If the domain was verified (§6.1), the
`TXT` record alone keeps the name protected and the `CNAME` could stay, but a record pointing at
nothing is worth deleting on a site whose whole point is that it can be removed.

