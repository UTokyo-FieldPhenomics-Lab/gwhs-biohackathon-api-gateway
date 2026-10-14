# Tailnet: Online but Unreachable from Every Room

Date: 2026-10-14 02:30 JST
Topic: `ssh <user>@gwhs-host` stopped working from the annex, while the Tailscale admin console
reported the node online. Root cause, the two ways the host can be reachable, and the three checks
that tell the three legs apart. This is where the runbook's diagnosis points.

## Objective

A room machine lost its tailnet SSH to the gateway host. Nothing in the console was red: the node was
**online**, its address was unchanged, and **key expiry was already disabled**. The job was to find
which leg was broken without changing anything on a hunch.

## What was ruled out, and by what

| Suspect | Verdict | Evidence |
|---|---|---|
| Instance stopped | no | `gwhs-host:80` answered a TCP RST in 273 ms; a stopped instance black-holes instead |
| Elastic IP detached | no | `api.global-wheat.com` still resolved to it, and the RST came from that address |
| Node offline, or key expired | no | admin console: online, `Expiry disabled`, `tailscale ip -4` = `gwhs-host` |
| Security group | not this | it blocks the *public* path only (port 22 from `the campus client network` timed out — the rule is scoped to another address), and tailnet SSH never touches it |
| The room's LAN leg | no | from the room machine, `ping room-router-a` — the router's *own* tailnet address — answered in 0.26 ms |
| Firewall or kernel drops on the host | no | only Tailscale's own `ts-input`/`ts-forward` chains; `Ip InDiscards` flat during a 40-ping burst |

## The measurement that settled it

40 pings to `gwhs-host`, measured on the host:

```
tailscale0 rx_packets delta (11s, while 40 pings were sent): 0
Ip InDiscards delta: 0
```

**Zero packets reached the kernel.** That rules out the return path and any routing repair as the
mechanism, and it is why `ss` showed no half-open socket at all: `tailscaled` drops these packets in
userspace, before they are injected into `tailscale0`. Two facts then explain why:

| Fact | Value | Meaning |
|---|---|---|
| Host `RouteAll` (`--accept-routes`) | **false** | the host had never accepted a room's subnet route |
| `ip route get lab-3090` on the host | `via the VPC gateway dev ens5` | the VPC default gateway, so even a received packet had nowhere to reply |
| 30 days of `sshd` logins | **39 from `room-router-a`**, 0 from `lab-3090` | back when it worked, the source was the *router's* tailnet address |

The last row is the whole story: the room router used to **masquerade** its LAN traffic, so a packet
left it as a known tailnet peer and needed nothing from the host. The router's *IP masquerade* toggle
is now off — most likely cleared by the Apply that turned off WAN advertising and changed a static IP
in the same session — so the packet keeps its source `lab-3090`, which the host silently
dropped because it did not accept that subnet.

## The two ways a room can reach this host

| Room router "IP masquerade" | Host `--accept-routes` | Result |
|---|---|---|
| on | off | works; the host sees the router's address, so logs cannot tell room clients apart |
| off | **on** | works; the client's own address arrives — **the configuration chosen here** |
| on | on | works |
| off | off | **nothing from a room reaches the host** |

Chose the host side because it is one setting instead of six routers' settings, and because it keeps
the real client address in the host's logs. It was applied on the live host and verified:

```bash
sudo tailscale set --accept-routes=true      # persisted in /var/lib/tailscale/tailscaled.state
```

After it: `ping gwhs-host` answered 3/3, and the original command worked with
`source=lab-3090` in `$SSH_CONNECTION` — direct proof that the source address is preserved and
that the room leg was fine all along. `install-tailscale.sh` now runs `tailscale up --accept-routes`
for the same reason, and prints the accepted subnets when it finishes.

## The three checks, when a room cannot reach the host

1. **From a machine in that room:** `ping <tailnet address>`, then `ping <that room router's tailnet
   address>` (`room-router-a` for the annex). The router answering while the host does not means the
   room's leg out of the LAN is fine and the host is the suspect.
2. **On the host, while that ping runs:** `ip -s link show tailscale0`. If `RX` does not move, the
   packets never arrive and a router is dropping them — the `off`/`off` row above. If `RX` moves, the
   host is receiving and not replying, which is the same row from the other side.
3. **On the host:** `ip route show table 52`. The room's subnet must be listed; the installer prints
   those same prefixes at the end of its run.

A fourth fact worth remembering: **a room's SSH is slow because the path is relayed.** From the host,
`tailscale ping room-router-a` reports `via DERP(tok)` and `direct connection not established`, ~227 ms
of it spent in the relay; a room machine sees 271 ms end to end. Nothing bulk should cross this link.

## Open items

- The annex router's masquerade stays off by design; if anyone turns it back on, nothing breaks, but
  the host's logs stop distinguishing room clients.
- The other five rooms' routers may also have lost their masquerade in the same cleanup. It no longer
  matters for reaching *this* host, but each room's own tailnet SSH to anything else still depends on
  it.
- `room-router-f` (the room gateway) still advertises `the campus client network`; unrelated to this incident,
  and never seen as an approved route.
- Tailscale SSH (`--ssh`) is still off, deliberately, until an ACL rule restricts the tailnet.
