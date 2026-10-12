#!/usr/bin/env bash
# Install Tailscale on the gateway host and join the tailnet.
#
# Run it on the host, as root, during the first login:
#   curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/install-tailscale.sh | sudo bash
#
# With no pre-authorization key it prints a URL and waits while you approve the machine in a
# browser, then reports the address. Safe to run again: apt and `tailscale up` both converge.
#
# Deliberately plain — this is our own convenience, not a supported installer. Targets Debian 13
# (trixie); change CODENAME for another release.

set -euo pipefail

CODENAME=trixie
REPO=https://pkgs.tailscale.com/stable/debian

mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL "$REPO/$CODENAME.noarmor.gpg" | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL "$REPO/$CODENAME.tailscale-keyring.list" | tee /etc/apt/sources.list.d/tailscale.list

apt-get update -qq
apt-get install -y tailscale
systemctl enable --now tailscaled

tailscale up
tailscale ip -4
