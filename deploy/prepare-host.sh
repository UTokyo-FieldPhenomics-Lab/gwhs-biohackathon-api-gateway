#!/usr/bin/env bash
# Prepare the gateway host: install the tooling the API server needs, then clone this repository
# onto the data volume.
#
# Run it on the host, as root (the checkout ends up owned by the user you sudo from):
#   curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/main/deploy/prepare-host.sh | sudo bash
#
# Idempotent: apt and the uv installer converge, and an existing checkout is fast-forwarded
# rather than cloned a second time. Debian 13 ships no uv package, so it comes from the official
# installer, which puts it in ~/.local/bin.

set -euo pipefail

REPO_URL=https://github.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway.git
DATA_DIR=/srv/biohack
CHECKOUT="$DATA_DIR/gwhs-biohackaton-api-gateway"
TARGET_USER="${SUDO_USER:-root}"

# curl is deliberately not in this list: fetching this script already proved curl and a working
# CA bundle, and reinstalling them would only upgrade packages the image already chose. git is
# the one thing Debian 13 cloud images do not ship.
apt-get update -qq
apt-get install -y -qq git

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -n "$TARGET_HOME" ]] || { echo "error: no usable home directory for $TARGET_USER" >&2; exit 1; }

# The checkout carries the virtualenvs, which are the bulky part. The root volume is 8 GiB, so
# it belongs on the data volume; a missing mount there is worth saying out loud rather than
# discovering as a full disk during the event.
if [[ -z "$(findmnt -no TARGET "$DATA_DIR" 2>/dev/null)" ]]; then
  echo "warning: $DATA_DIR is not a mountpoint, so the checkout lands on the root volume" >&2
fi
install -d -o "$TARGET_USER" -g "$TARGET_USER" "$DATA_DIR"

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
echo "next: cd $CHECKOUT/gateway && uv sync"
