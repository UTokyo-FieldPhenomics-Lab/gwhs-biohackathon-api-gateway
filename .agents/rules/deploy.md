# Deploy Rules

Package: `deploy/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **systemd, not Docker, for the gateway.** Ship the unit file and the bootstrap scripts. A
  `gateway/Dockerfile` is added only if a hosted platform is ever used; it does not exist
  today.
- **Secrets are injected at deploy time.** A script reads such a value from the environment; the
  repository records the variable's *name* — in a committed `.env.example` — never a value, and
  only for the scripts that need one.
- **Host-private state lives outside the checkout**, in `$TARGET_HOME/.config/biohack-gateway/`, mode
  0700 and owned by the service user: `env` there is what the gateway's systemd unit reads through
  `EnvironmentFile=`, and `setup-gateway.sh` creates the directory and the file once, never
  overwriting either. Never inside the git working tree, because the one irreversible failure in this
  repository is a secret reaching git history, and `.gitignore` is a promise rather than a boundary —
  `git add -f`, an edited ignore rule, `git clean -fdx`, or archiving the checkout all walk past it.
  The checkout is also the thing that gets copied around; the state directory is read only by the host
  that owns it.
- **One artifact, one environment today.** Code and lock file are shared, and a second
  environment would differ only by configuration — bucket, distribution, domain. There is one at
  present (the Frankfurt host, its bucket, and one distribution), so nothing is duplicated by
  environment yet; a second environment is added when a rehearsal needs a bucket that is not the
  one participants read.
- **TLS terminates at the box** (Caddy, installed from `deploy/Caddyfile` by `setup-gateway.sh`).
  The backend surface is bound to a non-public interface, carries its own token, and is never
  reachable through CloudFront; which private transport provides that is a deployment choice.
- **A certificate is issued only for a name that already resolves to the host.** ACME attempts are
  the scarce resource — five failures an hour per account and hostname — so nothing starts the proxy
  before that holds: `setup-gateway.sh` compares the authoritative `A` record with the host's own
  egress address and aborts on a mismatch, deliberately without an override, since the only reason to
  want one costs a validation attempt. Port 80 must be open for the same reason even though every
  client uses HTTPS: the challenge and the redirect to HTTPS both live there, and 80 and 443 are open
  to the world because participants are everywhere — the key protects the surface, not the port.
- **The host cannot verify its own perimeter, and neither can a single check from outside.** A
  security group is a rule outside the host, so a check run on the host sees only its own success;
  reachability is confirmed from a machine elsewhere, which is why it is a runbook step rather than
  something a host script can assert. That check has to be read correctly, because the two failures
  mean opposite things: `Connection refused` says the group is open and nothing is listening yet,
  while `timed out` may be the checking network rather than the group — on 2026-10-14 one address
  timed out on 22 although the group allowed it. Confirm from a second network before changing a rule.
- **The gateway host is on-demand.** It runs during the event window and is stopped
  otherwise, so the proxy and the gateway are systemd units enabled at boot: starting the instance
  must restore the API surface with nobody logged in to do it by hand.
- **The host is reached deliberately, never by convenience.** Two paths are accepted: an inbound
  TCP 22 rule scoped to a known address, and the private overlay network, which removes the need
  for that rule. The SSH port is never open to `0.0.0.0/0`. An address-scoped rule is tolerable
  only because these images refuse password and keyboard-interactive authentication, so a key is
  the only credential.
- **Nothing on the host is the only copy of anything, with one named exception.** Code comes from
  git, the index from a publish, certificates from ACME, the parquet cache from the pinned upstream
  revision, and every secret from outside the repository. That is what makes replacing the instance an
  ordinary event, and it is why the host needs no second volume and pins no availability zone. The
  exception is the participant token pool (`$TARGET_HOME/.config/biohack-gateway/tokens.json`): it is
  authoritative on the host, because issuing a token from the machine that checks it removes a whole
  sync direction, and a token is re-issuable rather than irreplaceable. The price is stated rather than
  hidden — terminating the instance voids every participant's token — so a copy is refreshed off-host
  after any change, and that is one `scp`.
- **The S3 write grant is scoped to one dataset prefix.** The instance role stays read-only, and
  publishing is the only thing that writes.
- **The bucket is private and CloudFront is the only reader.** Block Public Access stays on and
  the only policy statement names the distribution through its origin access control. No object is
  ever made public: a public bucket would be a second path to the same bytes and a second cache to
  reason about, while `data.*` is the single path clients use.
- **The bucket sits in the region that publishes into it** — `eu-central-1` today — so a publish
  moves bytes inside one region: no cross-region transfer to pay for and no latency between the
  cached parquet and the `PUT`. Moving the bucket moves the publish host with it.
- **Object paths are write-once, so a change means a new path.** A routine publish never invalidates
  anything and objects carry a long cache lifetime. **The one exception is a break-glass:** an object
  already being served that is proven wrong — during development or a rehearsal, or a published
  dataset found incorrect before it is republished — may have that path invalidated deliberately.
  Invalidate the paths that are actually wrong and not a wildcard over a prefix: the charge is per
  path (the first 1,000 a month are free, and one `*` counts once however many objects it matches),
  and a prefix whose contents all change is a new dataset id, not an invalidation. Invalidation
  repairs served bytes after the fact; it is never how a change is made.
- **The dataset bucket and its CDN are created by hand, once, from
  [`../../deploy/README.md`](../../deploy/README.md).** A bucket, a distribution, a certificate, and
  a DNS record are account-level resources used once each; a script that created them would on a
  second run rewrite a live distribution. The host scripts (`prepare-host.sh`,
  `install-tailscale.sh`, `setup-gateway.sh`) are the idempotent ones, because a host is rebuilt.
- **The host is reached from a room through exactly one of two switches.** Either the host accepts the
  room subnets (what `install-tailscale.sh` sets, and the configuration here, so a room client keeps
  its own address) or every room's router masquerades its LAN traffic. With both off, the host reports
  itself online while no room can reach it, and every symptom matches a host that is down. Tailnet
  flags live in that script — `tailscale up` applies exactly the flags on its line, so a flag set by
  hand is cleared on the next run — and reachability is re-checked after every stop/start.
- **Rollback pins identity, not a checksum we compute.** Code by git tag, container images by
  digest, model weights by an immutable upstream reference — the model repository's
  revision/tag or the S3 object version. Never generate and maintain our own checksum file;
  an image digest is the registry's content identifier, not a hash we compute.
- **Scripts are idempotent.** A second invocation converges instead of failing or
  duplicating resources.
