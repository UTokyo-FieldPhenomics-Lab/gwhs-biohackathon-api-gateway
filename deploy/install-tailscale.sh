#!/usr/bin/env bash
# Install Tailscale on the gateway host and join the tailnet.
#
# Run it on the host, as root, during the first login:
#   curl -fsSL https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackathon-api-gateway/main/deploy/install-tailscale.sh | sudo bash
#
# With no pre-authorization key it prints a URL and waits while you approve the machine in a
# browser, then reports the address. Safe to run again: apt converges, and `tailscale up` applies
# exactly the flags on its line below to an already-joined node.
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

# --accept-routes is what makes this host reachable from a machine in a room, and it is not
# cosmetic. The room routers do not masquerade their LAN traffic, so a client arrives with its own
# address (192.168.x.y) instead of the router's tailnet address; a node that does not accept the
# room's subnet route drops those packets inside tailscaled. The symptom is deceptive: the host
# answers `tailscale status` as online and `tailscale ping` to the router works, while nothing from
# any room reaches it. That is exactly how it went dark on 2026-10-14.
#
# This script owns the node's flags: `tailscale up` applies exactly the flags on its line, so if a
# pref is ever set on this host by hand — Tailscale SSH, an exit node — add it here too, or the
# next run of this script will silently clear it.
tailscale up --accept-routes

echo
echo "tailnet address: $(tailscale ip -4)"
echo "  room machines SSH to that address; the Tailscale admin console is where this node's key"
echo "  expiry has to be disabled (expiry runs on the clock, not on activity)."
echo
echo "Subnet routes accepted (a room whose subnet is missing here cannot reach this host unless its"
echo "own router masquerades its LAN traffic):"
accepted="$(ip route show table 52 2>/dev/null | awk '$1 ~ /\// && $1 !~ /\/32$/ {print "  " $1}' || true)"
if [[ -n "$accepted" ]]; then
  echo "$accepted"
else
  echo "  none — check that the room router advertises and has had its subnet route approved"
fi
