#!/usr/bin/env bash
# Put the API surface on the host: TLS in front of api.global-wheat.com, and the gateway under systemd.
#
# Run it on the host, as root, after api.global-wheat.com resolves to this host and ports 80/443 are open:
#   curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/setup-gateway.sh | sudo bash
#
# Idempotent: apt converges, the Caddyfile and the unit are rewritten from the checkout, and the
# services are restarted — so re-running this after a `git pull` is how a change takes effect.
#
# Two things it deliberately does not do. It cannot see whether the security group accepts
# connections from outside, because that is a rule on the host's perimeter: from in here the host
# only ever sees its own success, so the script prints the check to run from elsewhere. And it never
# touches the participant token pool: the gateway reads that file on its first authenticated request,
# so an absent pool is a valid state — nobody has been issued a token yet — and minting tokens stays
# a human command (see .agents/rules/deploy.md).

set -euo pipefail

API_DOMAIN=api.global-wheat.com
CHECKOUT=/opt/gwhs-biohackathon-api-gateway
UV=/usr/local/bin/uv

# Resolve the checkout from the script's own path when it is run as a file, and fall back to the
# installed location when it arrived on stdin, where BASH_SOURCE is unset.
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]:-}" 2>/dev/null || true)"
if [[ -f "$SCRIPT_PATH" ]]; then
  REPO_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")/.." && pwd)"
else
  REPO_DIR="$CHECKOUT"
fi

# Preflight
#
# An ACME validation is the scarce resource here: five failures an hour are allowed per account and
# hostname, and a certificate is only issued for the name the challenge resolves. Starting the
# proxy for a name that does not yet point at this host spends one attempt to learn nothing. There
# is deliberately no override, because the only reasons to want one are not worth an attempt.
[[ -d "$REPO_DIR/gateway" ]] || { echo "error: no gateway/ under $REPO_DIR — run prepare-host.sh first" >&2; exit 1; }
[[ -x "$UV" ]] || { echo "error: uv is not at $UV — run prepare-host.sh first" >&2; exit 1; }

HERE="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
ANSWER="$(curl -fsS "https://dns.google/resolve?name=$API_DOMAIN&type=A" \
  | grep -oE '"data":"[0-9]{1,3}(\.[0-9]{1,3}){3}"' | head -n1 | cut -d'"' -f4)"
if [[ -z "$HERE" || -z "$ANSWER" ]]; then
  echo "error: could not read this host's address or the A record for $API_DOMAIN" >&2
  exit 1
fi
if [[ "$HERE" != "$ANSWER" ]]; then
  cat >&2 <<EOF
error: $API_DOMAIN resolves to $ANSWER, but this host is $HERE.

  Fix the record at the registrar, wait out its TTL, and check again — a resolver still holding the
  old address fails the challenge and spends one of the five hourly attempts:

    curl -s 'https://dns.google/resolve?name=$API_DOMAIN&type=A'
    curl -s https://checkip.amazonaws.com
EOF
  exit 1
fi

echo "note: this script cannot verify that ports 80 and 443 accept connections from outside, because"
echo "      the security group sits outside the host. Confirm from another machine before trusting"
echo "      the result:  nc -vz $API_DOMAIN 443"

# Caddy
#
# Only gnupg, of the packages the vendor's instructions list: apt carries its own HTTPS transport
# now, and the keyring below is this repository's own signed-by key, so Debian's keyrings have no
# part to play. The vendor key is armored and apt verifies with gpgv, which cannot dearmor it.
# Redirecting into the keyring truncates it rather than prompting, which is what keeps this
# idempotent.
apt-get install -y -qq gnupg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor > /usr/share/keyrings/caddy-stable-archive-keyring.gpg
chmod 0644 /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list > /dev/null
chmod 0644 /etc/apt/sources.list.d/caddy-stable.list
apt-get update -qq
apt-get install -y -qq caddy

install -m 0644 "$REPO_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile
# reload-or-restart is a graceful reload on a running Caddy and a start on a stopped one, so
# comparing the old and new file buys nothing: an unchanged configuration reloads to the same thing.
systemctl reload-or-restart caddy

# Gateway
TARGET_USER="$(stat -c %U "$REPO_DIR")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" ]] || { echo "error: no usable home directory for $TARGET_USER" >&2; exit 1; }

# As the owning user, not as root: the virtualenv is theirs, and a root-owned one is what makes the
# next `git pull && uv sync` fail.
sudo -u "$TARGET_USER" -H "$UV" --directory "$REPO_DIR/gateway" sync --frozen

# The state the unit reads, outside the checkout, owned by the user it runs as: settings in `env`,
# created here, and whatever the gateway keeps beside it later — the participant key pool above all
# (.agents/rules/deploy.md). The directory, `publish/`, and `env` are created, and nothing that
# exists is overwritten: re-running this must never touch a value that is already there.
STATE_DIR="$TARGET_HOME/.config/biohack-gateway"
if [[ ! -d "$TARGET_HOME/.config" ]]; then
  install -d -m 0755 -o "$TARGET_USER" -g "$TARGET_USER" "$TARGET_HOME/.config"
fi
install -d -m 0700 -o "$TARGET_USER" -g "$TARGET_USER" "$STATE_DIR"
# The unit excludes this directory with InaccessiblePaths=, and systemd refuses a unit whose excluded
# path does not exist. Creating it up front makes the exclusion hold from the first start, before
# §5's credential exists — and excluding an empty directory is the safe default.
install -d -m 0700 -o "$TARGET_USER" -g "$TARGET_USER" "$STATE_DIR/publish"
if [[ ! -e "$STATE_DIR/env" ]]; then
  install -m 0600 -o "$TARGET_USER" -g "$TARGET_USER" /dev/null "$STATE_DIR/env"
  cat > "$STATE_DIR/env" <<'EOF'
# Settings for biohack-gateway.service. systemd hands this over through EnvironmentFile=. One
# VARIABLE=value per line, unquoted. The file is created even when nothing is set in it, because a
# missing state file must fail the start rather than let the service run with defaults.
#
# BIOHACK_BACKEND_TOKEN — the credential the gateway presents to a GPU backend as X-Backend-Token.
# It must hold the value those backends were started with (`-e BACKEND_TOKEN=…`), and it is the only
# lock on that hop: a backend sits behind a router whose address every tailnet peer wears, so nothing
# below the application can tell the gateway from any other machine on the overlay
# (deploy/README.md §1.3, deploy/gpu-backends.md). Unset, the gateway refuses every inference with
# 503 rather than letting a backend answer a 401 that reads like the participant's fault.
# BIOHACK_BACKEND_TOKEN=
EOF
fi

# User= names the owner of the checkout, because that is who owns the virtualenv the unit starts, and
# EnvironmentFile= follows their home. The committed unit carries this host's usual values so a
# hand-install works, and both lines are rewritten here so a rebuilt host needs no edit.
# `InaccessiblePaths=` takes a literal path rather than `%h`: that specifier is the home of the user
# running the *manager* (root here) and systemd documents it as unaffected by `User=`, so a `%h/…`
# names a directory that never exists and the unit fails at mount namespacing with 226/NAMESPACE.
# Rewriting the line keeps the exclusion pointing at this host's state directory.
UNIT_TMP="$(mktemp)"
sed -e "s|^User=.*|User=$TARGET_USER|" \
  -e "s|^EnvironmentFile=.*|EnvironmentFile=$STATE_DIR/env|" \
  -e "s|^InaccessiblePaths=.*|InaccessiblePaths=$STATE_DIR/publish|" \
  "$REPO_DIR/deploy/systemd/biohack-gateway.service" > "$UNIT_TMP"
install -m 0644 "$UNIT_TMP" /etc/systemd/system/biohack-gateway.service
rm -f "$UNIT_TMP"

systemctl daemon-reload
systemctl enable biohack-gateway
# restart, not `enable --now`: --now leaves an already running unit alone, so a second run after a
# `git pull` that changed the unit would report success without applying it.
systemctl restart biohack-gateway

# Smoke
for unit in caddy biohack-gateway; do
  systemctl is-active --quiet "$unit" || { echo "error: $unit is not active — journalctl -u $unit" >&2; exit 1; }
done

# One request covers the whole chain: DNS, the security group, the certificate, the proxy, and the
# gateway. It asks `/health`, which is the one route that needs no key, so the script never has to
# hold a participant credential to check that the stack is up — and unlike `/`, which answers 401
# now that keys exist, it says something about the application rather than only about Caddy.
#
# Two answers are worth waiting for, because both are transients a fresh run can hit. Until the
# certificate exists the TLS handshake has nothing to present, and until the gateway's socket
# accepts Caddy answers 502 — and that second gap is structural rather than unlucky, because
# `systemctl restart` returns when the process has been executed, not when it listens. A security
# group that never opened fails the same way as the first case, one connect timeout per attempt.
#
# Anything else is the gateway itself answering, so 200 is the only acceptable outcome: a 401 or a
# 404 here means the host is running a build that has no `/health`, and that is worth failing on
# immediately rather than sleeping through.
STATUS=""
for attempt in $(seq 1 12); do
  STATUS="$(curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{http_code}' "https://$API_DOMAIN/health" || true)"
  if [[ -n "$STATUS" && "$STATUS" != "000" && "$STATUS" != "502" ]]; then
    break
  fi
  if [[ "$attempt" == 1 ]]; then
    echo "waiting for the certificate and the gateway to come up…"
  fi
  sleep 5
done
if [[ -z "$STATUS" || "$STATUS" == "000" ]]; then
  echo "error: no answer from https://$API_DOMAIN/health — check DNS, the security group, and journalctl -u caddy" >&2
  exit 1
fi
if [[ "$STATUS" != "200" ]]; then
  echo "error: https://$API_DOMAIN/health answered $STATUS, not 200 — journalctl -u biohack-gateway" >&2
  exit 1
fi

echo
echo "https://$API_DOMAIN/health answered 200 with a certificate the client accepted."
echo "  certificate: journalctl -u caddy -n 30   (Caddy renews it on its own)"
echo "  gateway:     journalctl -u biohack-gateway"
echo "  settings:    $STATE_DIR/env   (created empty; the variables arrive with the settings layer)"
echo "  tokens:      biohack-gateway tokens add --id <team>   (everything but /health and /docs needs one)"
