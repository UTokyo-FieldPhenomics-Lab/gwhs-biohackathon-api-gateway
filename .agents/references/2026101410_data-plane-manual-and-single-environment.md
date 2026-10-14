# Data Plane: By Hand, One Bucket, and the Permissions It Needs

Date: 2026-10-14 10:00 JST
Topic: settling how the image plane gets built — with a script or by hand — and whether it needs a
dev environment; then rewriting `deploy/data-plane.md` as an explicit one-time manual and trimming
it of everything that duplicated a rule.

## Objective

`deploy/data-plane.md` had landed the previous day as a runbook with a rule beside it saying the data
plane "is created by hand". Two questions followed: should that be a script instead, and does a
single production bucket change the answer? The second question turned out to answer the first.

## Decided

| Decision | Reason |
|---|---|
| **By hand, once**, framed as such in the file's title | A bucket, a distribution, a certificate, and a DNS record are created once and never deleted. A script buys automation that gets used once, and its second run would rewrite a live distribution. |
| **No second environment.** One bucket, one distribution | Doubling the plane would double the resources, the certificate, the domain, and the record for the benefit of rehearsing against a private bucket. |
| The rehearsal target is **`gwhs-temp` in the live bucket** | Ten rows through the same code path, the same distribution, and the same path shape, for ten objects. This is what the dev-only dataset was for, and it is the compensation for having no dev bucket. |
| The publisher's grant is **a second, narrower policy** | `s3:PutObject` and `s3:GetObject` on one dataset prefix, carried by the publish run and date-bounded. A `HEAD` is authorized by `s3:GetObject`; there is no separate `s3:HeadObject` action. Never `DeleteObject` — the publisher removes nothing. |

What dropping the second environment costs, stated plainly: the publish is never rehearsed against a
bucket participants cannot read. The compensating facts are that objects are written once, that a
path collision raises at build time rather than overwriting, and that the rehearsal above exercises
every step except isolation.

## The permission set, and its shape

Three statements: the bucket's own operations scoped to `arn:aws:s3:::<bucket>`, the hand-placed test
object scoped to `<bucket>/*`, and CloudFront/ACM/STS at `Resource: "*"` because those APIs support
no resource-level ARN. Two actions earn their place by removing a decision:

- `s3:ListAllMyBuckets` tells *"the name is taken by us"* from *"taken by someone else"*. Only the
  first may be treated as "use what exists"; the second is a dead end, and mixing them up is the
  likeliest way a create-if-missing script does the wrong thing.
- `sts:GetCallerIdentity` lets the bucket policy's `AWS:SourceArn` be built from the real account id
  instead of a transcribed one.

Four things are deliberately absent so that a slip is impossible at the permission layer:
`cloudfront:CreateInvalidation` (nothing is ever invalidated), `cloudfront:DeleteDistribution`,
`s3:DeleteBucket`, `route53:*` (the zone is not in Route 53), and CloudFront access logging.

## What the manual gained and lost

Added, because a human following it needs them and a script would not have: the permission JSON with
the notes above, an ordered checklist matching the section numbers, a step that says where to record
the five values the run produces (bucket, OAC id, distribution id, certificate ARN, distribution
domain), and one boundary sentence — **the gateway needs only the `data.*` base URL**, never the
bucket name, because bytes never traverse it.

Removed, because each duplicated a rule that already exists and duplicated facts drift: the section
restating the two object headers (now a link to `datasets.md`), the paragraph deliberating
`PriceClass` and `Compress` (now two lines), the cost section (now one line, with the numbers in
`202610131500`), and the restatement of the architecture invariants in the opening.

**The line count did not fall as much as predicted**: 206 → 252. Three JSON templates are 65 of
those lines, and they are the part a human actually pastes. Extracting them to `deploy/*.json` would
bring the document to about 185 and make them `sed`-able, at the cost of introducing files that are
neither the procedure nor its rules; not done, and left as a decision rather than an oversight.

## Also changed

- `.agents/rules/project.md`: "dev and prod use separate buckets" became **"one bucket and one
  distribution per environment, and a dev pair never faces participants"**, plus the rehearsal
  sentence naming `gwhs-temp`. The byte-comparability bullet followed: with one environment its
  reason is that a republish is reproducible, not that dev can be compared with prod.
- `.agents/rules/deploy.md`: "two environments, one artifact" became **"one artifact, one environment
  today"**, keeping the rule for the second one rather than describing one that does not exist.
- `deploy/README.md`, `AGENTS.md`, `docs/data-layout.md`: "the dev host" → "the host", and "identical
  in dev and prod" → "identical in every environment". `README.md` needed nothing; it never claimed
  two buckets.

## Verified

- The three JSON blocks parse, the nine shell blocks pass `bash -n`, and every link in the touched
  files resolves.
- `datasets`: 14 passed, 4 deselected (offline); `uv run pytest -m network` 4 passed; `mypy` and
  `ruff` clean. Nothing in this change touches code.
- **Not verified: the permission policy.** No account was available to attach it to, so it is a
  reviewed design, not a tested one. The first person to run the steps is the one who finds out.

## Superseded later the same day: one deploy README, and a console procedure

Two more changes, both from reading the result rather than from a failure:

- **`deploy/` holds one document again.** The file this entry describes was itself the odd one out:
  `deploy/` is where a human goes once, and it had grown a second manual beside `README.md`, splitting
  one moment (the registrar: `api`'s `A` record here, `data`'s `CNAME` there) across two files. The
  procedure moved into `deploy/README.md` as sections 2 and 3, `deploy/data-plane.md` is gone, and the
  dead `.gitkeep` went with it. The CLI form survives in git: `git show 1a8d3e7:deploy/data-plane.md`.
- **The procedure is written for the console, not for the CLI.** The first draft was a shell runbook,
  which was the wrong shape for a human clicking through AWS once: the console creates the origin
  access control *inside* the distribution wizard, hands over the bucket policy text on the success
  page, and takes the alternate domain name and certificate as two fields at creation — so the whole
  `get-distribution-config` / `--if-match ETag` round trip, the `dist.json` template, and a separate
  OAC id to record all disappear. What survives from the CLI shape is the part that is not a command:
  a table of values to end up with, and the reason each one is what it is. The section is titled by
  what it builds (`The dataset bucket (S3) and its CDN`) rather than by an architecture term, because
  "image plane" told a newcomer nothing.
- **`datasets/.cache` became `datasets/.hf_cache`.** The old name collided with the generic `.cache`
  ignore rule and said nothing about who fills it; the new one sits beside `.mypy_cache`,
  `.pytest_cache`, and `.ruff_cache`, and the ignore line is now load-bearing rather than redundant.
- **The runbook's IMDS one-liner was wrong on the live host.** `curl
  http://169.254.169.254/latest/meta-data/public-ipv4` answers **`401`** there, because instances now
  launch with `Metadata version: V2 only`. The address check in 1.4 is now
  `curl -s https://checkip.amazonaws.com` alone — one source for one fact, verified on the host
  alongside the console's Elastic IP — and the metadata variant was dropped rather than repaired with
  a token dance. The finding is here so nobody re-adds it: if the instance's own view is ever needed,
  it takes a `PUT /latest/api/token` first.
- **§2.2's registrar note was rewritten from a real attempt, not from the AWS docs alone.** Steps 2.1–2.2
  were run against the live account, and three things only turned up by doing it: the zone's NS is
  `01–04.dnsv.jp`, so **Create records in Route 53** is not merely inapplicable but absent; お名前.com's
  「ホスト名」 field takes the **label only** (its own guide: for `xxx.domain.com`, enter `xxx`), so pasting
  ACM's whole name produces `…global-wheat.com.global-wheat.com`; and the CNAME **value** goes in without
  the trailing dot. §2.2 now says all three, and adds the `dns.google` query so the record is confirmed
  live before anyone waits on ACM — validation is polled by ACM, there is no button for it, and
  `ResendValidationEmail` is email-validation only. The lesson that cost the most time was ours: a DNS name
  was read off a screenshot whose text caret sat on top of one character, and three rounds of external
  queries were run against a name that did not exist. Take the name and value from **Export to CSV**.
- **The distribution steps were rewritten against the console that exists now.** The wizard asks for a
  name and a billing tier, preconfigures the origin and cache settings for an S3 origin, and takes a
  domain inline only for a Route 53 zone — so the old field list (viewer protocol policy, allowed
  methods, cache policy, price class, alternate domain name, certificate, and *"there is no reason to
  edit a distribution afterwards"*) described screens that were not there. §2.3 now names the fields
  that are shown and records the preconfigured values it applies instead of asking for them (redirect to
  HTTPS, `GET, HEAD`, `CachingOptimized`, Origin Shield off, all edge locations); §2.4 became a
  confirmation rather than a paste, because `Grant CloudFront access to origin: Yes` writes the bucket
  policy — its JSON was deleted rather than kept as a second path, and lives in this repository's
  history (`git log -- deploy/README.md`); §2.5 absorbed the certificate step, which is now
  post-creation, and with it the `dns.google` check used for the ACM record. Billing is recorded as
  **pay as you go**: every flat-rate tier attaches an AWS WAF web ACL and forbids removing it, while
  this distribution wants WAF off, and CloudFront's always-free tier (1 TB out and 10M requests a
  month) already covers 0.9 GB.
- **The permission policy is gone from the runbook, and its rule was simplified with it.** There is one
  operator, he is the account owner, he both builds the data plane and writes the publisher, and the
  repository is reference material for other people — nobody else can reach the account. Copying out a
  policy that is a subset of the permissions the same person already holds buys nothing, and it was the
  runbook's only step that sent the reader away to the IAM console. `deploy/README.md` now states which
  credential to run the steps with and what the procedure never does (invalidate, delete, touch Route
  53) — as a memo — and the JSON, its four notes, and the *"get an identity with the permissions
  below"* lead-in are gone: 315 → 267 lines. The rule lost its date condition for the same reason: a
  rule demanding an STS grant and an expiry for a publisher that does not exist yet is work the next
  reader would have felt obliged to do. What remains is one sentence — publishing writes to a single
  dataset prefix and the instance role stays read-only — which is exactly what the publisher needs when
  it is written. Revisit if this stops being a one-person prototype: the JSON is in this repository's
  history (`git log -- deploy/README.md`), and the argument for it is *"an account owner is not a
  principal that should be writing to an event bucket unattended"*.

Verified after the merge: both JSON blocks parse, every shell block passes `bash -n`, no file outside
`.agents/references/` still names `data-plane.md` or "image plane", and the datasets gates stay green.

## The cache lifetime, and the one invalidation

Step 3 of the runbook checks that every object carries `Cache-Control: public, max-age=31536000,
immutable`, and the objects hand-uploaded to `dev/` did not — which raised the fair question of whether a
year is right when CloudFront's own default is a day. The year stays, and the number was never the point:

- **The invariant is `immutable` plus a written-once path.** With those two, a cached copy is correct at
  every instant, and no correctness depends on the TTL.
- **What the number controls** is how often an edge revalidates against S3 — a conditional `GET`, no
  body — and with it a standing dependence on the origin plus cents a month of requests. A day is too
  slow to matter and permanent once adopted: a wrong object would sit at the edge for up to a day, which
  is useless during a five-day event, while the daily revalidation is forever.
- **A day would repeat a shape this repository already rejected.** The metadata index left S3 precisely
  to avoid an `index_etag` and "two freshness clocks that disagree" (`202610122120`). Clients holding a
  year while the edge holds a day is the same shape.
- **What the discussion actually exposed was a missing capability, not a wrong number.** Nothing could
  repair an object that is already being served and turns out to be wrong: `rules/deploy.md` said "no
  invalidation is ever issued", a rule about how changes are *made* doing duty as a rule about how
  mistakes are *repaired*.

So written-once paths and the long cache stay, a routine publish still invalidates nothing, and a
proven-wrong object **may** be invalidated deliberately — first 1,000 paths a month free — during
development, in a rehearsal, or for published data found incorrect before it is republished. The
publisher's own account gains nothing: it still never overwrites and never deletes.

Two smaller findings from the same thread are in `rules/datasets.md` now:

- **A byte comparison cannot see a header.** The `dev/` objects have no `Cache-Control`, and a re-run
  would have called them correct because size and `ETag` matched. The comparison now includes the two
  headers the publisher owns (one `HeadObject` returns all four fields), and equal bytes with different
  headers are repaired by re-`PUT`ting the same bytes — not an overwrite.
- **A mismatch on bytes is a contract break, not a refresh.** The run stops and names the key, and the
  operator chooses between a new dataset id and a deliberate wipe of a prefix still under development.
  It is not friction in the ordinary loop: an interrupted run resumes, a re-run with unchanged bytes
  skips, and a changed key format simply orphans the old keys.

## Open items

- **Steps 2.1–2.5 are now executed against the live account** (2026-10-14): the bucket
  `biohackathon-datasets` exists in `eu-central-1`, the certificate for `data.global-wheat.com` is
  issued, distribution `E3ES6IR9OQQJHZ` (`d3b65x3k45rqqn.cloudfront.net`) serves it with that
  certificate, the bucket policy was written by CloudFront, and `data.global-wheat.com` answers from
  the edge — `Miss` then `Hit` — while the S3 endpoint stays `403`. `api.global-wheat.com` already
  resolves to the Elastic IP. Still owed: TLS in front of `api.*` (Caddy and ACME on the host), the
  systemd unit, and the publisher itself, which does not exist yet.
- `202610122311` describes the host as the "dev host" with the December region open. That is a dated
  record and stays as written; this file supersedes it on both points.
- A publish that has to correct earlier output still needs a new path or a new dataset id — the
  consequence of frozen paths. The break-glass above repairs an object already being served; it is not
  the normal route, and the first real publish going wrong is the moment to remember both.
