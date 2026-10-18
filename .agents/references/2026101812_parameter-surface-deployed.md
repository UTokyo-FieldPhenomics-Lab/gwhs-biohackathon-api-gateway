# Deploying the parameter surface: gateway, three containers, one site

2026-10-18, 03:20–03:55 UTC (12:20–12:55 JST). Everything from
[`plans/2026101811_✅model-parameter-contract.md`](../plans/2026101811_✅model-parameter-contract.md)
went from commits to the live pool: the gateway that validates a query against each model's own
declaration, the container that declares five parameters and four sizes, and the site that generates
its table from that declaration.

## What was shipped, in the order it was

| Step | What | Evidence |
|---|---|---|
| 1 | Submodule pushed (`391f1b0`), parent pointer bumped in its own commit, parent pushed | `git push` twice; pointer recorded **after** the submodule, per `rules/models.md` |
| 2 | Host: `git pull --ff-only`, `uv sync --locked`, `systemctl restart biohack-gateway` | `/openapi.json` no longer lists `prompt` under predict; `ModelPrediction` has `warnings` |
| 3 | `labpc` (3090) re-created from the locally built image | `/health`: five parameters, `values` [672, 784, 896, 1008] |
| 4 | `homepc` (4090): archive → build → re-create | `/health` 200 with the new declaration |
| 5 | `lab 5090` (Windows/WSL): archive → build → re-create | `/health` 200 with the new declaration |
| 6 | **Gateway restarted again, after the containers** | `GET /models`: five parameters, three backends up |
| 7 | Docs site: the push triggered `.github/workflows/docs.yml` | builds `make_models.py` before Zensical and diffs the model pages |

**The order is not cosmetic.** Containers first, gateway last, because a declaration is read once and
never re-read (see below). Host before containers would also work — the old gateway forwards anything
— but then the new `/play` renders two controls until the containers land.

## The two traps

**1. A gateway caches declarations, so an image change ends with a gateway restart.** All three
backends were rebuilt and answering, `GET /models` still showed `prompt` and `confidence_threshold`,
and a `boxes` call answered `400 boxes is not a parameter`. `_declare` sets `backend.declared = True`
and nothing re-reads it while the process lives; `rules/gateway.md` states this on purpose ("a
declaration is a specification, and one that changes under a running process is a deployment that has
not been restarted"). Restarting the gateway after the containers is now a sentence in
[`deploy/gpu-backends.md`](../../deploy/gpu-backends.md) §3.3.

**2. `-p 127.0.0.1:12711:12711` is not the run line this pool uses.** The first re-create on `labpc`
copied the wrong part of `docker inspect`: `HostConfig.PortBindings` shows `"HostIp": ""` for a
container published on every interface, and the new one was created with loopback only — so the host
answered `000` for `lab-3090:12711` while the container looked healthy from its own machine.
The run line is `-p 12711:12711`; the check is the one the deploy doc already names — prove it **from
the gateway host**, never from the machine.

## Verified through the live gateway

A temporary team (`ops-verify`) was minted with `biohack-gateway tokens add`, used for one pass, and
removed in the same script; the pool is back to `dev` alone.

| Check | Answer |
|---|---|
| `GET /models` | `sam3.1-meta`, `backends_up` 3, `declared` true, parameters `prompt, boxes, points, confidence_threshold, imgsz`; machines `lab-5090`, `lab-3090`, `homepc-4090` |
| text only, `gwfss` row 4, `confidence_threshold=0.25` | 200, **17** objects at 1008 on `lab-5090` (the 3090 answers 18 on the same call — the card difference `deploy/gpu-backends.md` records) |
| text + `boxes=[[0,0,211,165,1]]` + `imgsz=2000` | 200, 21 objects, ran at 1008, `warnings: [resolution_clamped]` |
| `imgsz=320` | 400, "imgsz is one of 672, 784, 896, 1008" |
| `device=cuda` | 400, "device is not a parameter; this model accepts prompt, boxes, points, confidence_threshold, imgsz" |

## What the machines look like afterwards

- `labpc`: image rebuilt locally several times during P2; the running container is the last build.
- `homepc`: built from `sam31_official-391f1b0.zip`, 2 minutes with the weight layer cached.
- `lab 5090`: same archive; the build had to run in the **foreground** — a `nohup … &` through
  `ssh → wsl` died before docker started and left a zero-byte log, which read as "still building" for
  five minutes. It took 2 m 28 s with the cache and 36 s to unpack.
- `postgres`-less as before: nothing new on disk beyond the images.

## What the build actually did, and the one correction to this record

The builds were slow enough to look like a re-download. They were not:

| Observation | Number |
|---|---|
| Weights layer in the rebuilt image | **3.5 GB, 25 hours old** — reused |
| Code layer | **98.3 kB**, the only layer that changed |
| Image total | 10.0 GB on `labpc`, 13.9 GB on the 5090 |
| The 5090's build | 2 m 28 s total: **exporting layers 111.1 s + unpacking 36.4 s** |
| A weight fetch measured on that machine | 3.5 GB in **3 m 33 s** |

A re-fetch alone would have cost more than the whole build, so it did not happen: BuildKit spent the
time re-materialising a 10–14 GB image into the docker store, which scales with image size and not
with what changed. If releases ever get frequent enough for 2–3 minutes a machine to hurt, the two
levers are mounting the code into the container (a change becomes a restart) or moving the weights to
a volume (the image shrinks by 3.5 GB) — both trade away "the image carries its own checkpoint".

**Corrected 2026-10-18:** an earlier version of this file said `SAM31_RESOLUTION` was "still set on all
three run lines". It was in the **image**, from `Dockerfile`'s `ENV` block — `docker inspect <name>`
resolves a *container*, and the running container had inherited it, which is what I read. `docker
image inspect sam3.1-meta:latest` is the question that was being asked. The variable is gone from the
Dockerfile now, and `tests/labels.py` is what stops the next dead one from surviving a rebuild.

## Left as it was

- `SAM31_RESOLUTION` is gone from the image source but still baked into the three **running**
  containers, which ignore it. It disappears from an image the next time one is built; removing it from
  the running containers is a re-create for a variable nothing reads, so it waits for the next deploy
  for another reason.
- The site deploy is a workflow, not a hand act: it builds the parameter table from the recorded
  fixture and fails on a diff, so a page that disagrees with the declaration cannot publish.
