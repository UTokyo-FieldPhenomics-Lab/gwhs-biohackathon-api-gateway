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

## Left as it was

- `SAM31_RESOLUTION` is still set on all three run lines and is now **ignored**: the container reads
  `SAM31_SIZES`, whose default is the same four sides with 1008 as the ceiling. Removing it means
  re-creating three containers for a variable that does nothing; it stays until the next re-create for
  another reason, and the README's run line never had it.
- The site deploy is a workflow, not a hand act: it builds the parameter table from the recorded
  fixture and fails on a diff, so a page that disagrees with the declaration cannot publish.
