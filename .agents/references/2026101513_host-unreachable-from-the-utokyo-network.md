# Why this campus could not reach the host: an accepted tailnet route ate the replies

Date: first measured 2026-10-15 13:10 JST; root cause found and fixed 2026-10-16 11:25 JST.
Topic: "`api.global-wheat.com` is unreachable from the UTokyo network" — read for two days as a
campus-side drop of the Elastic IP. Its one cause, the one-command proof, the fix, and the lesson.

## The cause

The `room-router-f` GL.iNet router (`GL-MT6000`, `room-router-f`) advertised **`the campus client network`**
— the campus NAT every UTokyo client egresses from — alongside its own `a room LAN`. The host
runs `tailscale up --accept-routes` (`RouteAll: true`), so tailscaled installed
`the campus client network dev tailscale0` in table 52, and `ip rule` 5270 (`from all lookup 52`, ahead of the
main table) applied it to every unmarked packet — including the replies the kernel generates for
inbound connections:

```
0:     from all lookup local
5210:  from all fwmark 0x80000/0xff0000 lookup main     # tailscaled's own packets: unaffected
5270:  from all lookup 52                               # everything else: campus /24 goes to the tunnel
32766: from all lookup main
```

A campus client's SYN or ICMP echo arrived at `ens5`, the host built the reply, and the reply left
**into the tailnet** instead of back out `ens5` to `the campus client network`. The peer that owns the route *is*
`the campus client network`, so the reply was handed to that router's own WAN address and never reached the
client: silent timeout on every port and protocol, from the whole campus, for every address the
instance has ever had. tailscaled's own WireGuard packets carried fwmark `0x80000`, which `ip rule`
5210 sends to the main table — which is why the tailnet kept working from the same campus network the
whole time.

The proof is one capture on all interfaces, run on the host while pinging from this workstation:

```
ens5       In  IP the campus client network > the gateway host: ICMP echo request,  id 38627, seq 1
tailscale0 Out IP the gateway host > the campus client network: ICMP echo reply,   id 38627, seq 1
```

## The fix

At the source: the `the campus client network` advertisement was turned off on `room-router-f`
(`the room gateway`), tailscaled withdrew its route, and the public path returned. **The host carries no
configuration for this.** Two temporary host-side workarounds were applied while diagnosing and then
deleted: `ip rule add to the campus client network lookup main pref 5260` (5260 is free — tailscaled owns
5210/5230/5250/5270), and `ip route replace the campus client network via the VPC gateway dev ens5 … table 52`. The
gateway in the second is required: `dev ens5` alone yields `scope link`, the kernel ARPs for
`the campus client network` on the VPC subnet, and the replies vanish a second way.

Verified after the cleanup, from this campus, plain DNS and no workaround:

| Check | Result |
|---|---|
| `ping -c 4 api.global-wheat.com` | 4/4, ~301–318 ms |
| `GET /health` / `GET /` / `GET /docs` | `200` / `401` / `200` |
| `ip route get the campus client network` | `via the VPC gateway dev ens5` (main table, not `tailscale0`) |
| `ip route get lab-3090` | `dev tailscale0 table 52` — compute plane unchanged |

The lasting constraint is in [`../rules/deploy.md`](../rules/deploy.md): a router advertises the LAN
behind it, never the WAN it egresses through.

## What the earlier reading got wrong

The 10-15 record concluded "the campus firewall drops our new public address" — a correct set of
external measurements plus one wrong inference. Everything it measured still holds (foreign nodes
reach `:443`, the SG allows `0.0.0.0/0`, the certificate is valid, `gwhs-host` in the same `/16`
answers from here, traceroute dies after `58.138.83.x`), but none of it locates the drop, and the
correlation with the Elastic IP move was a coincidence: the advertisement appeared in the same window,
and the block is keyed to the *source*, not the destination. The earlier auto-assigned `a client`
was reachable on `:22` before that advertisement existed.

- **Capture on the destination host before blaming a network.** `tcpdump -ni any` shows
  request-in/reply-out-elsewhere in one command. A probe from one side cannot separate "never
  arrived", "a stateful firewall dropped it", and "the reply left by the wrong interface".
- **Read the host's route to the *source* address.** `ip route get <client-ip>` answers it, and
  `ip rule show` explains which table won. `--accept-routes` makes every advertised prefix a candidate
  reply path, and a /24 covering your own clients is self-inflicted.

`a client`, asked about on 10-16 as a replacement address, was never part of this: an
unassociated AWS-assigned EC2 address that answers nowhere in the world, including from the host. No
address change was needed and none was made; the EIP `gwhs-host` stays.

## A private fallback, if the public path ever breaks again

`curl --resolve api.global-wheat.com:443:gwhs-host https://api.global-wheat.com/health` reaches
the same Caddy and certificate over the tailnet. A browser needs the *name* on the wire, which takes a
host mapping, and the rest of that path (why an IP literal fails, which browser flags it needs) is
where the frame work lives:
[`2026101522_interactive-key-in-the-reference.md`](2026101522_interactive-key-in-the-reference.md).

## Do not

- Conclude a remote network is blocking you from probes taken on that network alone. Capture on the
  destination host; check `ip route get <source>` there.
- Re-open the security group, edit DNS, or swap the Elastic IP because a probe from here failed.
