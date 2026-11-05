# GPU allocation for the event

**2026-11-06.** The pool's final allocation: 13 GPU model ids across eight on-prem cards, plus one
rented AWS card if capacity allows. Constraints are
[`rules/backend-pool.md`](../rules/backend-pool.md) and [`rules/models.md`](../rules/models.md); what
was measured today is in
[`references/202611060345_aws-gpu-capacity.md`](../references/202611060345_aws-gpu-capacity.md).
Two of the three new ids have no container yet — `models/` holds `sprout/gwfss`, `sam31_official` and
`yolo_ultralytics` only — so their footprints are estimates and marked so.

## What has to be served

| id | resident + one request, GB | runs on a V100 | note |
|---|---|---|---|
| `sam3.1-meta` | 5.5 | no | bf16 autocast, Ampere and above |
| `sam3.1-gwhs` | 5.5 (est.) | no | GWHSS fine-tune, same container shape |
| `internvl-u` | 13.9 | no | bf16 weights, no measured fp16 |
| `wevisone-vl-4b` | 15.2 | yes | fp16 patch, 25 s per frame |
| `gwfss-segformer-b4` | 2.1 | yes | |
| `sprout-l-gwfss` | 3.2 | yes | |
| `sprout-v2-gwhs` | 3.2 (est.) | yes | GWHSS fine-tune |
| `tasselnetv4` | 0.9 | yes | |
| `dinov3-vitl16` | 1.2 | yes, `DINOV3_DTYPE=float32` | fp16 answers NaN on Volta |
| `dinov3-vith16plus` | 2.7 | yes, ditto | |
| `yolo26-ultralytics` | 1.0 | yes | |
| `yolo26-seg-ultralytics` | 2.0 | yes | |
| `yolov11-gwhs` | 2.0 (est.) | yes | GWHSS fine-tune |

Total 58.4 GB. The three `nano-banana` ids are hosted and take no GPU.

## The allocation

Ports: the model's own number — `12711` sam3.1-meta, `12712` gwfss, `12713` tassel, `12714` dinov3-L,
`12715` dinov3-H+, `12716` internvl, `12717` yolo, `12718` yolo-seg, `12719` sprout, `12720` wevisone
— and `12811` sam3.1-gwhs, `12812` sprout-v2, `12813` yolov11 for the three new ids. A multi-card host
uses `12700 + 10 × card + slot` for the first ten and `12800 + 10 × card + index` for the last three.

| site | link | containers | GB / budget |
|---|---|---|---|
| `lab-5090` | campus, fast and lossy | sam3.1-meta · sam3.1-gwhs · gwfss · tassel · sprout-v2 · yolov11 | 18.2 / 31 |
| `lab-3090` | campus, fast and lossy | sam3.1-gwhs · gwfss · sprout-l · dinov3-L · dinov3-H+ · yolo · yolo-seg · tassel · yolov11 | 19.6 / 23 |
| `homepc-4090` | stable | internvl-u · gwfss · tassel · yolov11 | 17.9 / 23 |
| `lab-intel3090` | Starlink, stable and slow | internvl-u · sam3.1-meta · tassel | 20.3 / 23 |
| `dgx-v100-0` | Starlink | gwfss · tassel · dinov3-L · dinov3-H+ · yolo · yolo-seg · sprout-l · sprout-v2 · yolov11 | 17.3 / 31 |
| `dgx-v100-1` | Starlink | wevisone · gwfss · tassel · dinov3-L · sprout-v2 | 22.6 / 31 |
| `dgx-v100-2` | Starlink | wevisone · dinov3-H+ · sprout-l · yolov11 | 22.1 / 31 |
| `dgx-v100-3` | Starlink | wevisone · tassel · yolo · yolo-seg · sprout-v2 | 22.3 / 31 |
| **AWS card** | same VPC, lowest RTT | variant A: sam3.1-meta · sam3.1-gwhs · gwfss · tassel (card 1) and sprout-l · sprout-v2 · dinov3-L · dinov3-H+ · yolo · yolo-seg · yolov11 (card 2) | 14.0 and 14.3 / 21.5 |

Copies: tassel 9, gwfss 6, sprout-v2 6, yolov11 6, sprout-l 4, each dinov3 4, each yolo 4,
sam3.1-meta 3, sam3.1-gwhs 3, wevisone 3, internvl 2.

**The rule the layout follows.** A link that is fast and lossy gets the short answers, where a retry
costs a second; a link that is slow and steady gets the slow models, where its latency is noise. The
rented card is the shortest path of all because it shares the gateway's VPC, but an L4 is a 3090-class
card, so it buys availability and latency, not compute — `internvl-u` and `wevisone-vl-4b` are absent
from it for that reason.

**What is not served where it would be fast.** `internvl-u` has only two copies, both on stable links
(`homepc-4090` at 11 s and `lab-intel3090` at 50 s), because 13.9 GB does not fit beside either
sam3.1 on a 23 GB card. `wevisone-vl-4b` stays on the V100s at 24 s; moving it to the 4090 would cost
internvl its fast path for a smaller gain.

## The rented card: two variants

| | A — 2 × `g6.xlarge` (L4, 21.5 GB each) | B — 1 × `g7e.2xlarge` (RTX PRO 6000, 95 GB) |
|---|---|---|
| ids served | 11 (all but the two heavy VLMs) | 13, on one card at 61% |
| price, eu-central-1 | $0.98/h each | $5.72/h on-demand, $3.25/h spot |
| 24/7 for five days | $235 | $686 on-demand, $390 spot |
| the event's 40–45 h | $78–88 | $229–257 on-demand, $130–146 spot |
| capacity on 2026-11-06 | **L4 launched in 1b and 1c** | **no capacity in 1a/1b, not offered in 1c** |

Variant A is the plan of record because it can actually be launched, fits the $200–300 budget even
running around the clock, and leaves both VLMs on links the pool already trusts. Variant B is strictly
better where it runs — one node, every model, 2× the card — and becomes affordable because the event
is only 40–45 hours of daytime; it is waiting on capacity, not on money or quota.

**Promotion rule:** if `deploy/probe-gpu-capacity.sh` ever reports a g7e launch, hold that instance
instead of terminating it and re-plan around it. A successful probe is evidence that capacity existed
for one minute, not a reservation.

## The event's clock

| day | hacking | online with margin |
|---|---|---|
| Mon 07 | 15:30–18:00 | ~4 h |
| Tue 08 – Thu 10 | 09:00–18:00 | ~10–11 h each |
| Fri 11 | 09:00–12:30 | ~4–5 h |

About 40–45 hours in total. Under variant A the card runs all week ($235); under variant B it is
started before Monday's session and stopped after Friday's, which is what brings $686 down to $257.

## What still has to happen

1. The three new ids need containers, then images, then the rows above. Build once per link domain:
   the DGX builds and pushes to `lab-intel3090` over the room LAN (118 MB/s); `lab-5090` and
   `lab-3090` build locally on the campus downlink. No image on the V100s or the Intel box is
   re-downloaded.
2. `homepc-4090`'s containers are stopped (deliberately, 2026-11-03) and `internvl-u` there needs the
   image rebuilt from submodule `80d84dd` for its 120 s response budget.
3. The AWS security group needs `12711`–`12723` from the gateway's group, not `12711`–`12713`.
4. `models.json` gains one URL per new (machine, model); the gateway restarts; `/pool` is the check.
5. A G7e vCPU quota case, if variant B is still wanted — the probes prove the account can request one
   (no `VcpuLimitExceeded`), but the quota for the family has never been read directly.

## The first g7e, and the one-week probe

`deploy/probe-gpu-capacity.sh` asks every ten minutes between 09:00 and 15:00 JST (01:00–07:00 in
Frankfurt) and stops itself on 2026-11-13: the question is what the small hours look like, not a
permanent watch. A miss is retried ten minutes later; a launch cools the probe for an hour, because a
second question so soon after a hit says nothing new. That cadence is what makes the log a record of
availability *density* rather than of two arbitrary moments.

Every launch it makes is a probe and is terminated within seconds — except the first `g7e.*`, which is
held for **at most 60 minutes** and then destroyed whatever the trial reached. On that one instance the
user-data runs the real deployment: the trial image (a Blackwell-capable Deep Learning base image, not
the pool's Debian one whose driver predates the card), ECR login through the instance profile, pull of
the six images ECR carries, all six containers on one card, and one timed call per model. The report
goes to the serial console and is saved to `~/.local/state/gwhs/g7e-trial-<timestamp>.log`; the
availability log is `~/.local/state/gwhs/gpu-capacity.log`. The trial happens once — a marker file
keeps later g7e sightings as plain probes.

The four ids a trial cannot cover are those with no ECR image (`sprout`, both `yolo26` ids) and the two
that are not planned for the rented card at all (`wevisone`, and the three GWHSS fine-tunes that do not
exist yet); the report says so instead of omitting them.
