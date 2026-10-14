#!/usr/bin/env bash
# Prepare the gateway host: install the tooling the API server needs, then clone this repository
# into /opt.
#
# Run it on the host, as root (the checkout ends up owned by the user you sudo from):
#   curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/prepare-host.sh | sudo bash
#
# Idempotent: apt and the uv installer converge, and an existing checkout is fast-forwarded
# rather than cloned a second time. Debian 13 ships no uv package, so it comes from the official
# installer, which puts it in ~/.local/bin.

set -euo pipefail

REPO_URL=https://github.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway.git
CHECKOUT=/opt/gwhs-biohackathon-api-gateway
TARGET_USER="${SUDO_USER:-root}"

# curl is deliberately not in this list: fetching this script already proved curl and a working
# CA bundle, and reinstalling them would only upgrade packages the image already chose. git is
# the one thing Debian 13 cloud images do not ship.
apt-get update -qq
apt-get install -y -qq git

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" ]] || { echo "error: no usable home directory for $TARGET_USER" >&2; exit 1; }

# One disk is enough: the checkout is under a megabyte and the virtualenvs add a few hundred, all
# of which fit the root volume. Nothing on this host is the only copy of anything, so there is
# nothing worth giving its own volume to (see .agents/rules/deploy.md).
install -d -o "$TARGET_USER" -g "$TARGET_USER" "$CHECKOUT"

UV="$TARGET_HOME/.local/bin/uv"
if [[ ! -x "$UV" ]]; then
  echo "installing uv for $TARGET_USER"
  sudo -u "$TARGET_USER" -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
# systemd does not inherit a login shell's PATH, so uv also goes where every caller looks first.
ln -sfn "$UV" /usr/local/bin/uv
"$UV" --version

if [[ -d "$CHECKOUT/.git" ]]; then
  sudo -u "$TARGET_USER" -H git -C "$CHECKOUT" pull --ff-only
else
  sudo -u "$TARGET_USER" -H git clone "$REPO_URL" "$CHECKOUT"
fi
[[ "$(stat -c %U "$CHECKOUT")" == "$TARGET_USER" ]] || chown -R "$TARGET_USER:$TARGET_USER" "$CHECKOUT"

echo
echo "next: bind api.global-wheat.com to this host, then start it (deploy/README.md §1.4 → §1.5):"
echo "  sudo bash $CHECKOUT/deploy/setup-gateway.sh"
