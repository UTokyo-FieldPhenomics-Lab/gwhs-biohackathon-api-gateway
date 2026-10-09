# Deploy Rules

Package: `deploy/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **systemd, not Docker, for the gateway.** Ship a unit file and one bootstrap script. A
  `gateway/Dockerfile` is added only if a hosted platform is ever used; it does not exist
  today.
- **Secrets are injected at deploy time.** When the deploy scripts land they include a
  committed `.env.example` that lists variable names only, never values.
- **Two environments, one artifact.** dev and prod share code and lock file and differ only
  by configuration: separate buckets, distributions, and domains.
- **TLS terminates at the box** (Caddy or nginx). The backend surface is bound to a
  non-public interface, carries its own token, and is never reachable through CloudFront;
  which private transport provides that is a deployment choice.
- **The gateway host is on-demand.** It runs during the event window and is stopped
  otherwise.
- **Rollback pins identity, not a checksum we compute.** Code by git tag, container images by
  digest, model weights by an immutable upstream reference — the model repository's
  revision/tag or the S3 object version. Never generate and maintain our own checksum file;
  an image digest is the registry's content identifier, not a hash we compute.
- **Scripts are idempotent.** A second invocation converges instead of failing or
  duplicating resources.
