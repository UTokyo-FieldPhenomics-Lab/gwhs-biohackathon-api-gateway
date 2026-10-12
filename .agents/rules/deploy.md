# Deploy Rules

Package: `deploy/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **systemd, not Docker, for the gateway.** Ship the unit file and the bootstrap scripts. A
  `gateway/Dockerfile` is added only if a hosted platform is ever used; it does not exist
  today.
- **Secrets are injected at deploy time.** A script reads such a value from the environment; the
  repository records the variable's *name* — in a committed `.env.example` — never a value, and
  only for the scripts that need one.
- **Two environments, one artifact.** dev and prod share code and lock file and differ only
  by configuration: separate buckets, distributions, and domains.
- **TLS terminates at the box** (Caddy or nginx). The backend surface is bound to a
  non-public interface, carries its own token, and is never reachable through CloudFront;
  which private transport provides that is a deployment choice.
- **The gateway host is on-demand.** It runs during the event window and is stopped
  otherwise.
- **The host is reached deliberately, never by convenience.** Two paths are accepted: an inbound
  TCP 22 rule scoped to a known address, and the private overlay network, which removes the need
  for that rule. The SSH port is never open to `0.0.0.0/0`. An address-scoped rule is tolerable
  only because these images refuse password and keyboard-interactive authentication, so a key is
  the only credential.
- **Nothing on the host is the only copy of anything.** Code comes from git, the index from a
  publish, certificates from ACME, the parquet cache from the pinned upstream revision, and every
  secret from outside the repository. That is what makes replacing the instance an ordinary
  event, and it is why the host needs no second volume and pins no availability zone.
- **The S3 write grant is time-bounded.** The instance role is read-only; the publishing run
  carries a policy restricted to a single prefix and expiring on a date condition, so no
  manual revocation step can be forgotten.
- **The bucket is private and CloudFront is the only reader.** Block Public Access stays on and
  the only policy statement names the distribution through its origin access control. No object is
  ever made public: a public bucket would be a second path to the same bytes and a second cache to
  reason about, while `data.*` is the single path clients use.
- **The bucket sits in the region that publishes into it** — `eu-central-1` today — so a publish
  moves bytes inside one region: no cross-region transfer to pay for and no latency between the
  cached parquet and the `PUT`. Moving the bucket moves the publish host with it.
- **Object paths are write-once, so there is nothing to invalidate.** Objects are served with a
  long cache lifetime and no invalidation is ever issued; a path that has to change becomes a new
  path at publish time.
- **The data plane is created by hand, once per environment, from
  [`../../deploy/data-plane.md`](../../deploy/data-plane.md).** A bucket, a distribution, a
  certificate, and a DNS record are account-level resources used once each; a script that created
  them would on a second run rewrite a live distribution. The host scripts
  (`prepare-host.sh`, `install-tailscale.sh`) are the idempotent ones, because a host is rebuilt.
- **Rollback pins identity, not a checksum we compute.** Code by git tag, container images by
  digest, model weights by an immutable upstream reference — the model repository's
  revision/tag or the S3 object version. Never generate and maintain our own checksum file;
  an image digest is the registry's content identifier, not a hash we compute.
- **Scripts are idempotent.** A second invocation converges instead of failing or
  duplicating resources.
