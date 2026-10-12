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
  publish, certificates from ACME, and every secret from outside the repository. That is what
  makes replacing the instance an ordinary event, and it is why the host needs no second volume
  and pins no availability zone. Scratch space for a publish is a volume that is discarded with
  the run.
- **The S3 write grant is time-bounded.** The instance role is read-only; the publishing run
  carries a policy restricted to a single prefix and expiring on a date condition, so no
  manual revocation step can be forgotten.
- **Rollback pins identity, not a checksum we compute.** Code by git tag, container images by
  digest, model weights by an immutable upstream reference — the model repository's
  revision/tag or the S3 object version. Never generate and maintain our own checksum file;
  an image digest is the registry's content identifier, not a hash we compute.
- **Scripts are idempotent.** A second invocation converges instead of failing or
  duplicating resources.
