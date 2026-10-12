# The Data Plane

The image plane: a private S3 bucket behind CloudFront, served on `data.*`. The gateway never
carries bytes — it answers with the public `data.*` URL of an object. Binding constraints are in
[`../.agents/rules/deploy.md`](../.agents/rules/deploy.md); the host that publishes into this plane
is brought up by [`README.md`](README.md).

## What exists, and where

| | |
|---|---|
| Bucket | `<project>-data-<env>`, private, `eu-central-1` — the region that publishes into it |
| Access | Block Public Access on, no public policy; CloudFront reaches the bucket through an origin access control (SigV4) and is the only reader |
| Distribution | one per environment, `data-<env>.<domain>`, HTTPS only, `GET`/`HEAD`, long TTL, all edges |
| Objects | written once at publish and never overwritten, so nothing is ever invalidated |
| Certificate | ACM in **`us-east-1`** — CloudFront requires it there whatever the origin region is |
| DNS | `data-<env>` is a `CNAME` to the distribution domain, at the registrar; `api.<domain>` is an `A` record to the host's Elastic IP, with TLS terminated on the host |

dev and prod differ by bucket, distribution, and domain; the dev bucket never faces participants.
Bucket names, account ids, and resource ids are deployment values and stay out of the repository —
the commands below take them from the shell.

## 1. Bucket

```bash
export AWS_REGION=eu-central-1
export BUCKET="<bucket-name>"      # a deployment value: not committed
export DOMAIN="<full-domain>"      # for example data-dev.<domain>
export ORIGIN_ID="<origin-id>"     # any label, used to bind the behaviour to the origin

aws s3api create-bucket --bucket "$BUCKET" \
  --create-bucket-configuration LocationConstraint="$AWS_REGION"

aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

SSE-S3 is the default encryption and is enough: these objects are meant to be served to everyone
through the distribution, so a customer-managed KMS key would buy nothing. Versioning stays off — an
object path is written once, so there is no previous version to keep.

## 2. Origin access control and the distribution

```bash
OAC_ID=$(aws cloudfront create-origin-access-control \
  --origin-access-control-config \
  Name="${BUCKET}-oac",OriginAccessControlOriginType=s3,SigningBehavior=always,SigningProtocol=sigv4 \
  --query 'OriginAccessControl.Id' --output text)
```

The origin is the bucket's REST endpoint, never its website endpoint, which cannot be private. The
cache behaviour uses the managed `CachingOptimized` policy (`658327ea-f89d-4fab-a63d-7e88639e58f6`:
TTL 1 s / 1 day / 1 year) — an object's own `Cache-Control` overrides the default, which is why the
publisher sets one (§5). Save the block as `dist.json` with the placeholders substituted:

```json
{
  "CallerReference": "<bucket>-1",
  "Comment": "<bucket>",
  "Enabled": true,
  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "<origin-id>",
        "DomainName": "<bucket>.s3.eu-central-1.amazonaws.com",
        "OriginAccessControlId": "<oac-id>",
        "S3OriginConfig": { "OriginAccessIdentity": "" }
      }
    ]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "<origin-id>",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 2,
      "Items": ["GET", "HEAD"],
      "CachedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] }
    },
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
    "Compress": true
  },
  "HttpVersion": "http2and3",
  "PriceClass": "PriceClass_All",
  "Restrictions": { "GeoRestriction": { "RestrictionType": "none", "Quantity": 0 } },
  "ViewerCertificate": { "CloudFrontDefaultCertificate": true }
}
```

```bash
DIST_ID=$(aws cloudfront create-distribution --distribution-config file://dist.json \
  --query 'Distribution.Id' --output text)
```

`PriceClass_All` keeps every edge in play. Narrow it to `PriceClass_100` (Europe and North America)
if every participant is in Europe and the egress rate matters: which edges serve is a distribution
setting, not an object layout, so it changes without a republish. `Compress` is on because it costs
nothing and helps any text object; the images are already compressed and pass through.

## 3. Bucket policy

Only the distribution may read the bucket. Until this policy exists, CloudFront answers `403` for
every key: an OAC signature is honoured by the policy, not by the distribution.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontRead",
      "Effect": "Allow",
      "Principal": { "Service": "cloudfront.amazonaws.com" },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<bucket>/*",
      "Condition": {
        "StringEquals": { "AWS:SourceArn": "arn:aws:cloudfront::<account-id>:distribution/<dist-id>" }
      }
    }
  ]
}
```

```bash
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file://policy.json
```

## 4. Certificate and domain

```bash
CERT_ARN=$(aws acm request-certificate --region us-east-1 \
  --domain-name "$DOMAIN" --validation-method DNS \
  --query 'CertificateArn' --output text)
```

Add the validation `CNAME` it returns at the registrar, then wait for `ISSUED`:

```bash
aws acm describe-certificate --region us-east-1 --certificate-arn "$CERT_ARN" \
  --query 'Certificate.Status' --output text
```

Attach the alias and the certificate. A distribution is edited through its `ETag`, so read it
first and pass it back:

```bash
ETAG=$(aws cloudfront get-distribution-config --id "$DIST_ID" --query 'ETag' --output text)
aws cloudfront get-distribution-config --id "$DIST_ID" \
  --query 'DistributionConfig' > dist.json
# in dist.json: set Aliases = {"Quantity": 1, "Items": ["<domain>"]} and
# ViewerCertificate = {"ACMCertificateArn": "<cert-arn>", "SSLSupportMethod": "sni-only",
#                      "MinimumProtocolVersion": "TLSv1.2_2021"}  (drop the default certificate)
aws cloudfront update-distribution --id "$DIST_ID" --if-match "$ETAG" \
  --distribution-config file://dist.json
```

The zone is served by `dnsv.jp`, not Route 53, so the record is a `CNAME` rather than an alias:
`data-<env>` → `<distribution-id>.cloudfront.net`, TTL 3600.

**Do not point a certificate at a name that does not yet resolve where you think.** A stale or
wrong record costs a validation failure and burns one of the five hourly attempts; check
resolution first:

```bash
curl -s "https://dns.google/resolve?name=$DOMAIN&type=CNAME"
```

## 5. What the publisher must send

Two headers per object. Without them the objects are served, but badly:

- `Cache-Control: public, max-age=31536000, immutable` — a path never changes, so no viewer has to
  revalidate. CloudFront caches regardless, but a viewer without this revalidates on every request.
- `Content-Type` from the file extension — S3 stores no type of its own, and a browser will not
  display an object typed as `binary/octet-stream`.

Objects are small enough for a single request, which also keeps `ETag` equal to the content MD5 —
see [`../.agents/rules/datasets.md`](../.agents/rules/datasets.md).

## 6. Verify

Before the publisher exists, one object by hand proves the whole path:

```bash
aws s3 cp ./00000001.jpg "s3://$BUCKET/<dataset>/images/00000001.jpg" \
  --content-type image/jpeg \
  --cache-control 'public, max-age=31536000, immutable'

curl -s -o /dev/null -w '%{http_code}\n' \
  "https://$BUCKET.s3.$AWS_REGION.amazonaws.com/<dataset>/images/00000001.jpg"   # 403: private

curl -sI "https://$DOMAIN/<dataset>/images/00000001.jpg" |
  grep -iE '^(HTTP|cache-control|content-type|x-cache)'    # 200, the two headers, x-cache
```

Run the CloudFront request twice: the first answers `x-cache: Miss from cloudfront` and the second
`Hit`, which is the distribution serving from its cache rather than from the bucket. Since paths are
immutable, one key per dataset is enough to check that the prefix, the type, and the content length
are what the publisher was told to write.

## What this costs, in the shape it takes

1.88 GB of standard storage is cents per month, and 8,714 objects are cents to write. Storage and
requests are therefore noise; the only cost that scales with use is CloudFront egress, and it stays
off the gateway. Publishing happens from the bucket's own region, so a publish itself transfers
nothing across regions.
