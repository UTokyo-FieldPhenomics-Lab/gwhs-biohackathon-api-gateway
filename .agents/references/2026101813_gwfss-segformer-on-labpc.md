# The GWFSS SegFormer: built, measured, and serving on `labpc`

P1–P3 of [`plans/2026101813_gwfss-segformer-model.md`](../plans/2026101813_gwfss-segformer-model.md),
executed 2026-10-18. The container lives at `models/gwfss_segformer` (`391562c`); the parent pointer
is `24e7b2b`.

## What is serving

| | |
|---|---|
| id | `gwfss-segformer-b4` |
| host | `labpc` (`labpc-host`), `lab-3090` |
| host port → container port | `12712` → `12711` |
| image | `gwfss-segformer-b4`, built on `labpc` |
| declaration | `resolution: 512`, `parameters: []`, `max_in_flight: 1` |
| labels | resident `0.7`, per-request `1.4` GB |

`models.json` on the gateway host gained one line:

```json
"gwfss-segformer-b4": ["http://<address>:12712"]
```

## A port is the model's, not the machine's

Two containers may both listen on `12711` because each has its own network namespace; what routes is
the host side. The convention, now in `rules/backend-pool.md` and `deploy/gpu-backends.md`, is one
number per model on every machine: `sam3.1-meta` is `12711`, `gwfss-segformer-b4` is `12712`.

`sam31-test` — a loopback-only leftover on `12712` from an earlier session — was removed to free the
number. Nothing else listened there.

## Two traps, both invisible at first

**The classic builder does not run a `RUN` heredoc.** The first Dockerfile fetched its weights with
`RUN python - <<'PY' … PY`. The step succeeded in the log and baked nothing: the builder ran
`python -` with an empty standard input, so the script was a no-op. The image was 256 MB lighter than
it should have been and the failure appeared only when a container tried to load its processor. The
fetch is now `fetch_weights.py`, copied and run — and the build asserts the three files exist after
it. `docker version` reports 29.7.2 with the legacy builder (the log says `Removed intermediate
container`), and heredocs need BuildKit.

**A batch dimension survived one line too long.** `infer.class_map` interpolated and softmaxed a
`(1, 4, h, w)` tensor, then called `argmax(dim=1)[0]` (shape `(h, w)`) and `gather(0, …)` on the
still-4-D tensor. Every call raised `Index tensor must have the same number of dimensions`. The
warm-up would have raised too, which means the container would have answered `503` forever with a
stack trace in the log and no other symptom. `tests/bench.py` found it because it calls the same
path a request does, rather than only the forward pass.

## Measurements

Through the container's own `infer` and `outputs`, on the ten published rows, RTX 3090:

| | |
|---|---|
| mIoU (background / head / stem / leaf) | 0.701 (0.747 / 0.866 / 0.366 / 0.825) |
| pixel accuracy | 0.884 |
| one 512² call, whole path | **38.3 ms** median (forward alone was 24.8) |
| VRAM, weights loaded | +258 MiB above a loaded context |
| VRAM, 512² inference | +840 MiB, torch peak 697 MiB |
| the largest upload the guard allows (8000×6000) | 688 ms, **1539 MiB** torch peak, card +1930 MiB |

The per-request label is 1.4 GB because admission has to hold for the largest request a participant
can send, not for a dataset row. The upload guard is 50 MP; 8000×6000 is 48 MP.

## The deploy, in order

1. Build on `labpc`; `tests/labels.py` and `tests/outputs.py` run from the image with the repository
   mounted read-only.
2. `tests/bench.py` inside the image, with `--gpus all`, to produce the figures above.
3. Labels and `Settings` defaults updated to 0.7 / 1.4, rebuild.
4. `docker run -d --gpus all --restart unless-stopped -p 12712:12711 -e BACKEND_TOKEN=… -e
   GWFSS_MACHINE=lab-3090 --name gwfss-segformer-b4 gwfss-segformer-b4`. Ready in 4 s.
5. `models.json` edited on the gateway host, then `systemctl restart biohack-gateway` — **after** the
   container, because a declaration is read once and never re-read.

Verified through the gateway with a temporary `ops-gwfss` token, revoked immediately:

```
gwfss-segformer-b4  res 512  up 1  declared True  params []
sam3.1-meta         res 1008 up 3  declared True  params [prompt, boxes, points, confidence_threshold, imgsz]
```

`POST /models/gwfss-segformer-b4/predict {"dataset":"gwfss","row":4}` answered three objects —
head 88,848 px, stem 15,437, leaf 124,434, background 33,425 — in 25,711 bytes. `?prompt=x` was a
`400` at the gateway and again at the container, which refuses any query string on its own.

## The documentation

- `docs/scripts/fixtures/model-directory.json` re-recorded from the public API with a temporary token
  (`ops-record`, revoked); it now lists both models and `backends_up` 3 and 1.
- `docs/scripts/fixtures/gwfss-row4-prediction.json` is the recorded answer, 25.7 kB.
- `make_models.py` now iterates every model in the directory and **fails** on a model with no page, so
  a deployed id without documentation breaks the docs build instead of passing it. Its empty-parameter
  branch now reads `declared` and says "this model declares no parameters" rather than "no backend has
  declared this model yet".
- `make_figures.py` draws the model's classes beside the dataset's mask from the recorded answer, via
  the client's decoder. It needs `--with requests` for that import; the docstring records why.
- `zensical build --strict` is clean and `make_models.py` produces no diff.

## P4 — three machines

| machine | address | image | build |
|---|---|---|---|
| `lab-5090` | port `12712` | 18.1 GB | inside WSL, by `ssh → wsl -e bash` |
| `lab-3090` | port `12712` | 18.2 GB | on `labpc` |
| `homepc-4090` | port `12712` | 11.3 GB | on `homepc` |

The folder travelled as a 55 kB tarball: `scp` to the Linux host, and `cat | ssh → wsl` to the Windows
one, with the sha256 checked on the far side before extracting — the transfer goes through `cmd`, so
the check is the only thing that says the bytes arrived intact.

The 5090 answered on `12712` **without a new Windows firewall rule**. Docker Desktop's proxy adds a
rule per process rather than per port, so the rule published for `12711` covered the second container
too. Convenient, and not something to rely on: `deploy/gpu-backends.md` still says to add the port to
the rule when a published port does not answer.

`GET /models` then read `backends_up: 3` for both models. The same call on all three cards:

```
lab-5090    head 88,849  stem 15,437  leaf 124,432
lab-3090    head 88,848  stem 15,437  leaf 124,434
homepc-4090 head 88,848  stem 15,437  leaf 124,434
```

Scores are identical to three decimals; the masks differ by one or two pixels on the 5090. That is the
same card difference the SAM pool recorded as 18 objects versus 17, now measured on a second model.

## P5 — `/play`

Verified in a browser against the live gateway with a temporary `ops-play` token:

- the model list offered both ids, and the model line read `3 backends up · 3 requests at a time`;
- choosing `gwfss-segformer-b4` rendered **no parameter controls** — the legend stayed hidden and the
  parameter span had zero children — and the `curl` line carried no query string;
- `gwfss` row 4 painted a 512×512 canvas, and the page's own check read
  `3 objects · Σ decoded mask = 228,718 px, Σ area = 228,718 px — equal` (the 5090 answered).

`/play?model=<id>` now preselects the model the link names, and an id the directory does not list is
ignored rather than shown. The gateway host was pulled to `4a7d233` and restarted; the model page
links to the preselected page. The browser had no stored key before the test, the temporary key was
cleared afterwards, and `ops-play` was revoked — the pool holds `dev` alone again.

Pulling the parent repository on the gateway host prints `Could not access submodule 'models'` and
applies the change anyway: that checkout never initialises the submodule, and the gateway reads
nothing from it.

## Open

- **The full `gwfss` publish** — ten rows today, 1,096 before the event, as agreed.
- **B1** stays unserved and linked from the model page.
- <https://docs.global-wheat.com/models/gwfss-segformer-b4/> answers; the figure is served as a
  173 kB JPEG and the index lists the model.
