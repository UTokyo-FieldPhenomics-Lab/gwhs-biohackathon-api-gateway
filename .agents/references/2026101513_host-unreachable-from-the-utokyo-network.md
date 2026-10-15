# The host is unreachable from the UTokyo network, and from a room

Date: 2026-10-15 13:10 JST (the room failure was measured 2026-10-14 02:30; the campus workaround
was added 2026-10-15 23:15).
Topic: the two independent reasons a session here cannot reach the gateway host, the one path that
works from this campus, and the rules the conclusions feed.

**A workaround for one network, not a practice.** The project's way to the API is plain DNS to its
public address, and that way works everywhere measured except this campus network. Nothing in the
build, the deploy, or CI may depend on anything in this file.

## Conclusion: the campus firewall drops our new public address

From `visitor2.fm.a.u-tokyo.ac.jp` (`133.11.179.64`, AS2501) the Elastic IP `52.28.155.227` is
unreachable on every world-open port, while from abroad the same service answers `200` on `/health`
from de, es, ir, ru, se, and us, and while another address in the same `52.28.0.0/16`
(`52.28.52.157`) answers on `:443` from here. The name resolves, the security group allows 80 and 443
from `0.0.0.0/0`, and the certificate is valid, so the drop is **destination-specific and on this
network's path**: the campus blocks the address we were newly allocated. It appeared together with
the move to the Elastic IP — the instance's earlier auto-assigned address was reachable from here on
TCP 22 — so a probe that fails from a campus machine says nothing about the host.

That reading is what [`../rules/deploy.md`](../rules/deploy.md) means by "`timed out` may be the
checking network rather than the group": confirm from a second network before changing a rule, a
record, or anything on the host.

## The path that works from here

The host is also on the tailnet, so a live check from this campus goes over the private path:

```bash
curl --resolve api.global-wheat.com:443:100.88.197.115 https://api.global-wheat.com/health
```

The name has to stay on the wire: a URL that is a bare address sends no SNI, and `deploy/Caddyfile`
declares one site, so the handshake dies before any certificate is presented. For a browser, map that
one name — an `/etc/hosts` line, or a throwaway profile with
`--host-resolver-rules="MAP api.global-wheat.com 100.88.197.115"`. A frame then served from
`100.64.0.0/10` trips Chrome's private-network check
(`net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`), which is an artifact of the fallback address and
is disabled in that profile alone. Driving the framed reference from here also needs a browser that
waits in real time (Playwright against the system Chromium): a virtual time budget expires the parent
while the cross-origin child is still loading, so the height message never arrives and a working page
looks broken.

A pass on this path says nothing about a participant's experience — the path is private, not the
public one — so an acceptance check that has to reflect a participant runs from a network that
reaches the public address.

## The room leg is a separate cause

Tailnet SSH from a room stops working while the Tailscale console reports the node **online**, with
key expiry already disabled. That is not the firewall: a room's packets arrive carrying the client's
own LAN address, and the host answers them only if it accepts that room's subnet route. Either side
can supply it — the room's router masquerading its traffic, or the host accepting routes — and the
host side is what this deployment uses, because it is one setting instead of a router's per-site one
and it keeps the real client address in the host's logs. `deploy/install-tailscale.sh` applies
`tailscale up --accept-routes` for that reason, and the constraint is in
[`../rules/deploy.md`](../rules/deploy.md).

Two checks tell the legs apart: from the room, `ping` the room router's *own* tailnet address (an
answer means that room's leg out of the LAN is fine, so the host is the suspect), and on the host,
`ip -s link show tailscale0` while that ping runs (a flat `RX` means the packets never arrived).
A room's SSH is relayed and therefore slow; nothing bulk should cross it.

## Do not

- Turn any of it into a rule, a deploy step, or a CI step, beyond the two rules it already feeds.
- Re-open the security group, or edit DNS, because a probe from here failed: both were measured, and
  neither is the cause.
