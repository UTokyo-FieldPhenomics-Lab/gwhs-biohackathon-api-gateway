# `api.global-wheat.com` unreachable from the UTokyo visitor network, and nowhere else

2026-10-15 13:10 JST. The second time the campus path to the Elastic IP was measured rather than
guessed. The reading rule it feeds already exists in [`../rules/deploy.md`](../rules/deploy.md)
("`timed out` may be the checking network… confirm from a second network before changing a rule");
this file is the measurement that rule points at, plus the external vantage the first run
([`202610140230_tailnet-not-reachable-while-online.md`](202610140230_tailnet-not-reachable-while-online.md),
[`2026101415_caddy-and-systemd-on-the-host.md`](2026101415_caddy-and-systemd-on-the-host.md)) did not
have in one place.

## The question

Is `https://api.global-wheat.com/health` failing because of DNS, or because the campus blocks it?

## Answer, from evidence

Neither the name nor the service is at fault. The drop is on this one network path.

| Leg | Measurement | Result |
|---|---|---|
| Name | `getent hosts api.global-wheat.com`, `dns.google/resolve` | `52.28.155.227`, single A, no AAAA — **DNS is correct** |
| Service, end to end | check-host.net HTTP check on `/health`, 4 nodes (ir, ru, se, us) | `200`, `OK` — the gateway, Caddy and the certificate are all serving |
| Service, TCP | check-host.net TCP check on `:443`, 6 nodes | 5/6 connected in 1–87 ms; only `ro1` timed out |
| Service, TCP from Asia | check-host.net TCP `:443`, jp1/jp2/kr1/sg1 | jp1 `0.246 s`, sg1 `0.165 s` — **not a Japan→AWS problem** |
| This workstation | TCP connect to `52.28.155.227` on 22, 80, 443, 8000, 8080 | **all time out**, three times in a row, no RST |
| This workstation | `traceroute -n` to the EIP | last responder `58.138.83.169` (NTT), then `*` to hop 20 |
| This workstation | TCP `:443` to `ec2`/`s3`/`dynamodb.eu-central-1.amazonaws.com` (`3.78.204.255`, `3.5.136.237`, `35.71.105.130`) | **all OK** — AWS `eu-central-1` is reachable from here in general |
| ICMP, from abroad | check-host.net ping check, 6 nodes (de, es, ir×2, se, us) | **`OK` 4 replies each** — the host answers ping, so the group permits ICMP |
| This workstation | `ping 52.28.155.227` | 100 % loss, three runs |
| This workstation | TCP `:443` to `secretsmanager.eu-central-1.amazonaws.com` = **`52.28.52.157`** | **OK** — the EIP's own `/16` is reachable from here |

Source address of the failing probes: `133.11.179.64`, `visitor2.fm.a.u-tokyo.ac.jp`, AS2501 — the
same address whose `:22` timeout is already recorded in the 10-14 run.

The perimeter as it actually is, probed from abroad on 2026-10-15 (`check-host.net`, de/us/se/jp):

| Port | From abroad | Reading |
|---|---|---|
| 80, 443 | connected | open to `0.0.0.0/0`, as `deploy/README.md` §1.5 says |
| 22 | timed out | still scoped to one address, as documented |
| 9999 | timed out | dropped, so the group is not "all traffic" |
| ICMP | 4 replies each from **6 networks** | an ICMP rule exists — and **no document in this repository records one** |

That last row is what makes the historical `ping` ambiguous. On 2026-10-12/13 the workstation's own
scrollback shows `ping api.global-wheat.com` → `63.182.171.197` at **100 % packet loss** while SSH to
that same address succeeded (`admin@ip-172-31-6-113`, private `172.31.6.113`, "Connection to
63.182.171.197 closed"). So ping never worked on the old address either: what worked was TCP 22.
`63.182.171.197` was the instance's auto-assigned public IPv4, released when the Elastic IP replaced it,
so it cannot be retested now.

What that scrollback *is* good for: the campus path reached an AWS Frankfurt address of this same
instance before the EIP existed, on a port the group allows. The address that replaced it is dropped
here on every world-open port. The change of address and the change of reachability coincide in time,
which is why the EIP swap in "What to do" is the experiment that separates them rather than a
convenience.


DNSBL checks (`zen.spamhaus.org`, `b.barracudacentral.org`, `dnsbl.sorbs.net`) are clean, so a recycled
address with a bad reputation is not supported as the mechanism, though it cannot be ruled out for a
list only the campus consults.

## What this does not establish

- **That it is the campus firewall.** The packets stop between the last NTT hop and the destination.
  Campus egress filtering is the likeliest explanation, an upstream (SINET/NTT) destination filter and
  a source-based drop inside AWS are both consistent with the same symptom, and the security group is
  not the cause — it allows 80/443 from `0.0.0.0/0`, and the world connects.
- **Anything about the `52.28.0.0/16` range.** `52.28.0.1`, `52.28.155.1`, `52.29.0.1`, `52.58.0.1` all
  failed from here, but those are AWS-managed addresses with nothing listening, so they black-hole from
  any network. Only the EIP itself carries evidence, because only the EIP was proved reachable abroad.
  **The range is now cleared rather than merely unproven:** `secretsmanager.eu-central-1.amazonaws.com`
  answers on `52.28.52.157`, reachable from here on `:443`, so the block is destination-specific and not
  a prefix.
- **Where on the path the drop happens.** `traceroute -n` to the EIP and to the reachable
  `52.28.52.157` both stop at `58.138.83.x` and then go silent, so the trace does not separate a drop at
  AWS from a drop at a campus or upstream filter; unprivileged UDP traces cannot see past that hop for
  either destination.
- **Whether ping ever differed from TCP here.** It does not now: the host answers ICMP from six
  networks, so a `ping` that fails from this workstation carries the same information as a failing TCP
  connect, and a `ping` that once worked to the *previous* address only says that address was reachable —
  the security group is bound to the instance, so changing the address changes nothing on the host.
  Record the address and the measuring network when comparing a before/after, or the comparison is
  between two networks rather than between two addresses.

## What to do

0. **A working path already exists from this workstation, and it needs nothing new.** The GL.iNet
   router that serves this subnet is itself on the tailnet, and the host accepts the room's subnet
   route, so the LAN reaches the host privately: `100.88.197.115:22` and `:443` both connect from the
   workstation. Pointing the *public name* at the tailnet address exercises the real thing — same
   Caddy, same certificate, same key gate — without touching DNS or routing:

   ```bash
   curl --resolve api.global-wheat.com:443:100.88.197.115 https://api.global-wheat.com/health
   #   200 {"status":"ok","version":"0.1.0"}   tls verify 0
   curl --resolve api.global-wheat.com:443:100.88.197.115 https://api.global-wheat.com/     # 401
   curl --resolve api.global-wheat.com:443:100.88.197.115 https://api.global-wheat.com/docs # 200
   ```

   What this does not do is imitate a participant: the path is private, not the public one. For a
   keyed request over a real public route, use any non-campus network (home, a phone hotspot), or an
   exit node — see below.
1. **Reach the host over the tailnet**, which is the route chosen on 10-14 for exactly this reason. Do
   not spend time on DNS: it resolves.
2. **To separate "this IP is filtered somewhere" from "this path is filtered", move the EIP**: allocate
   another Elastic IP, reassociate, update the `api.*` A record, reissue the certificate, and measure
   from the same workstation. A working new address proves destination-IP filtering; an equally dead
   new address points at the path or the peer. One experiment, and it is reversible.
3. If it is reported to the campus, the useful payload is source `133.11.179.64`, destination
   `52.28.155.227`, ports 22/80/443, JST timestamps, and the traceroute that dies at `58.138.83.169`
   while the rest of `eu-central-1` answers.
4. Confirm a second network before touching host or group configuration — this file exists because the
   temptation was to re-open the security group that was never closed.
