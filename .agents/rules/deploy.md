# Deploy Rules

Package: `deploy/`. Shared constraints are in [`project.md`](project.md).

- **Deploy the gateway with systemd, not Docker.** Supply the unit and bootstrap scripts; a gateway
  Dockerfile is added only for an actual hosted-platform requirement.
  [Unit](../../deploy/systemd/biohack-gateway.service).
- **Inject credentials at deployment; commit names only.** Keep examples value-free. Host-private
  state lives outside git in the service user's `~/.config/biohack-gateway/`, directory mode 0700.
  Create existing state only once; reruns must not overwrite tokens, env or credentials.
  [Bootstrap](../../deploy/setup-gateway.sh).
- **Exclude the publisher's whole directory from the service.** Its `publish/env` and access-key CSV
  are separate from gateway `env`; `InaccessiblePaths=` is required because both processes run as
  the same user. [Isolation](../../deploy/systemd/biohack-gateway.service).
- **Keep environment differences in configuration.** One environment exists; create a second bucket
  and distribution only when an independent rehearsal needs them, not duplicated code or locks.
  [Architecture](project.md).
- **Terminate TLS on the gateway host with Caddy.** Bind backends to non-public access and require
  their own token; CloudFront never proxies inference. Open public 80/443 for ACME and participants,
  with authentication protecting application calls. [Proxy](../../deploy/Caddyfile).
- **Check authoritative DNS against host egress before starting ACME.** A mismatch aborts bootstrap
  without override, avoiding failed issuance attempts. [Bootstrap](../../deploy/setup-gateway.sh).
- **Verify reachability from outside the host, on more than one network when needed.** Host-local
  success does not prove its security-group perimeter; refused and timed-out connections imply
  different failure classes. [Reachability](../references/2026101513_host-unreachable-from-the-utokyo-network.md).
- **Bootstrap must assert public `/health` returns 200 without a participant key.** Retry TLS startup
  and proxy `502` transients, fail other outcomes; `/` answering `401` is not a liveness test.
  [Smoke check](../../deploy/setup-gateway.sh).
- **Enable proxy and gateway units at boot.** The on-demand host must restore service after start
  without an interactive login. [Runbook](../../deploy/README.md).
- **SSH is deliberate: overlay access or source-scoped TCP 22, never `0.0.0.0/0`.** Address-scoped
  access requires key-only SSH, with password and keyboard-interactive authentication disabled.
  [Host setup](../../deploy/prepare-host.sh).
- **Host replacement must be reconstructible.** Code comes from git, index/cache from pinned
  publishing, certificates from ACME and credentials from external state. No second volume or pinned
  availability zone is required. Tokens are the authoritative exception: maintain an off-host copy
  after changes; without it a replacement requires reissuing participant keys.
  [State recovery](../../deploy/README.md#where-the-gateways-settings-and-tokens-live).
- **Grant the publisher writes only to dataset prefixes; keep the instance role read-only.** The
  publisher reads its 0600 access-key CSV into a boto3 session; only its bucket, region and CSV path
  names enter `publish/env`. Do not export credentials into gateway env.
  [Credential setup](../../deploy/README.md#5-the-publish-credential).
- **Keep the bucket private with CloudFront OAC as its public read path.** Retain Block Public
  Access; do not create an anonymous S3 path beside `data.*`.
  [Data plane](../../deploy/README.md).
- **Publish in the bucket's region.** Moving the bucket moves the publish host/cache requirement,
  avoiding inter-region image transfer. [Region](../references/202610131500_data-plane-region-and-streaming.md).
- **Do not invalidate during routine publishing.** Changed content gets a new path. Break-glass
  invalidation may repair proven-wrong served paths, never a wildcard substitute for a new dataset.
  [Write-once layout](../../docs/data-layout.md).
- **Create account-level bucket/CDN/certificate/DNS resources manually once; make host scripts
  idempotent.** Host recreation must converge, but an ordinary rerun must not rewrite a live
  distribution. [Runbook](../../deploy/README.md).
- **Maintain one working room-to-host route mechanism.** The host accepts room routes or routers
  masquerade room traffic; keep tailnet flags in `install-tailscale.sh` and recheck reachability
  after stop/start. [Routing](../../deploy/install-tailscale.sh).
- **Never advertise a prefix containing public clients.** Advertise router LANs, not WAN egress
  ranges; otherwise accepted subnet routes can steal the host's reply path. Check `ip route get`
  before changing network policy. [Incident](../references/2026101513_host-unreachable-from-the-utokyo-network.md).
- **Rollback uses upstream identity, not new checksum files.** Pin code by git tag, containers by
  registry digest, and weights by the model repository's recorded immutable identity. Do not add
  locally maintained hashes. [Weight identity](models.md).
