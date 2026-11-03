# DINOv3 backbones: what was built, measured and deployed

Date: 2026-11-02 15:55 JST. Branch: `main`. Worked in `models/dinov3/` (the private submodule) and
`gateway/src/biohack_gateway/models.py`.

## What this adds

Two model ids serving Meta's DINOv3 encoders, both the **LVD-1689M** checkpoints — pretrained on web
images, not the `sat493m` satellite variant the store already held:

| id | checkpoint | dim | side | port | resident, bf16 |
|---|---|---|---|---|---|
| `dinov3-vitl16` | `facebook/dinov3-vitl16-pretrain-lvd1689m` | 1024 | default 512, max 768 | 12714 | 0.6 GiB |
| `dinov3-vith16plus` | `facebook/dinov3-vith16plus-pretrain-lvd1689m` | 1280 | default 384, max 640 | 12715 | 1.6 GiB |

One folder, one Dockerfile, two builds: `MODEL_ID`, `WEIGHTS_FILE_ID`, `WEIGHTS_BYTES`,
`CONFIG_DIR`, `DEFAULT_SIDE`, `MAX_SIDE`, `RESIDENT_GB` and `PER_REQUEST_GB` are the whole difference,
so the two images cannot drift in code. `rules/models.md` allows shared code across builds and forbids
one declaration serving two weight sets.

The answer is the model's own tokens, because DINOv3 has no head to produce anything else:
`embedding` is the CLS vector (1024 or 1280 floats) and `features` is the patch grid as base64 of
little-endian float16, row-major and channels-last, with `objects: []` so one client parses every
model. At the defaults the L sends 2.8 MB and the H+ 1.9 MB; at their ceilings both send about 6 MB.

## Why the decisions went this way

**LVD-1689M, not sat493m.** The satellite checkpoint is pretrained on overhead imagery; these
participants photograph wheat from a metre away. The store holds both, and both files are called
`model.safetensors`, so the build asserts the byte count — 1 212 559 808 for the L, 3 362 432 800 for
the H+. That assertion is what makes a swapped file id fail a build instead of an inference.

**The 7B checkpoint is out.** 26 864 210 088 bytes of float32, one card to itself, and 4096
dimensions — four times the payload of the L for gains the paper measures on datasets far larger than
an event's. It is a third build of the same Dockerfile the day somebody's experiment asks for it.

**base64 float16, not JSON lists.** A 32×32 grid of 1024-dim vectors is 1 048 576 numbers: 7–10 MB of
decimal text against 2 MB here, and half precision costs three digits that no similarity in this
application can resolve. `tests/outputs.py` decodes with the published recipe and compares.

**`side` is the longer side with the aspect ratio kept**, each dimension floored to a multiple of the
patch. A square resize would have been simpler and would have distorted every portrait photograph in
both datasets. Floored, not rounded: a caller's ceiling then cannot be exceeded by one patch row.

**Loaded in bfloat16.** The checkpoints are published in float32 and the paper's inference is mixed
precision; 0.6 GiB against 1.2 is the difference between fitting beside three other containers on a
24 GB card and not.

## What it measures

On `lab-3090`, through `infer.features`, one image per call:

| | load | 512² frame | 4:6 frame at 512 | peak, torch |
|---|---|---|---|---|
| `dinov3-vitl16` | 0.5–0.8 s | 38.6 ms | 27.2 ms | 604–641 MiB |
| `dinov3-vith16plus` | 0.5–0.7 s | — | 44.8 ms | 2 191 MiB |

End to end through the API, cold (image fetch included) and warm (cache hit): 2.3 s and 20 ms for
1.58 MB at `side=384`; 3.1 s for the 6.3 MB answer at `side=1024`, which clamps to 768.

## Deployment

`models.json` on `gwhs-host` gained two entries pointing at `lab-3090`, ports 12714 and 12715;
the gateway was restarted and both ids appear in `GET /models` with their `side` declaration read from
the container. The gateway contract change (two optional `ModelPrediction` fields) is `6a0d5d8`, the
docstring wrap `f15cd79`, both pulled onto the host.

Run lines:

```bash
docker run -d --gpus all --restart unless-stopped -p 12714:12711 \
  -e BACKEND_TOKEN=… -e DINOV3_MACHINE=lab-3090 --name dinov3-vitl16 dinov3-vitl16
docker run -d --gpus all --restart unless-stopped -p 12715:12711 \
  -e BACKEND_TOKEN=… -e DINOV3_MACHINE=lab-3090 --name dinov3-vith16plus dinov3-vith16plus
```

## What broke, and what to do differently

- **`docker build` produced no output for three minutes.** The command was `docker build … | tail -40`
  in the background, and the pipe buffers until the build ends. Redirect to a file instead; a build
  one cannot watch is a build whose failure one reads afterwards.
- **`DOCKER_BUILDKIT=1` fails on this host** — the buildx component is not installed. The classic
  builder works, ARG expansion in `COPY` included, and is what the sibling containers already assume.
- **`AutoImageProcessor` has no slow DINOv3 processor** — the saved one is
  `DINOv3ViTImageProcessorFast` and the fast path needs torchvision. Rather than add a package, the
  mean, std and resample filter are read from `preprocessor_config.json` and the three operations
  done in numpy. The resize is PIL's, not torchvision's antialias default; a fraction of a pixel per
  patch, and now a known difference rather than a hidden one.
- **The two Drive folders were listed in the order they were pasted, and the first was the H+.** Both
  files are called `model.safetensors` and neither name says which checkpoint it is; only the byte
  count does. Confirm a store file by size, never by position in a message.
- **One gateway test failed once under load** (`test_a_token_cannot_hold_more_than_three_requests_at_once`)
  while the H+ image was downloading; it passed alone and in the next full run (307 passed). Treat a
  lone failure in a timing test on a busy machine as a measurement artefact and re-run before
  investigating.

## ECR

The two images were pushed on 2026-11-03 from the build workstation (`linux/amd64`) as
`dinov3-vitl16:20261103` and `dinov3-vith16plus:20261103` in `eu-central-1`. Both repositories are
private, `IMMUTABLE`, and scan on push. The registry is the deployment's account, written here as
`<account>.dkr.ecr.eu-central-1.amazonaws.com`; the digest is what an AWS backend pins.

| id | tag | digest | compressed |
|---|---|---|---|
| `dinov3-vitl16` | `20261103` | `sha256:f345f4c14d72df1c9a07192f61be4458508f6e3555d8f1a2dd8469c40f55339b` | 7.73 GB |
| `dinov3-vith16plus` | `20261103` | `sha256:07bc5ee450372ce3db000a110721b411fa22cbb81e5190e96e23da0269f6511c` | 9.88 GB |

Pull by digest, not by tag:

```bash
docker pull <account>.dkr.ecr.eu-central-1.amazonaws.com/dinov3-vitl16@sha256:f345f4c14d72df1c9a07192f61be4458508f6e3555d8f1a2dd8469c40f55339b
```

The tag matches the other model images' date tags (`tasselnetv4:20261102`, `internvl-u:20261103`).
The AWS path's run line and security group are in [`deploy/gpu-backends-aws.md`](../../deploy/gpu-backends-aws.md);
this note records only what was published.

## What is not done

- **The docs site still says three models.** `docs/content/models/index.md` needs both ids, and each
  needs a page; `fixtures/model-directory.json` should be refreshed from a live `GET /models` first.
- **The clients do not decode `features`.** `rules/clients.md` now asks clients to own field decoding
  the way they own RLE; `decode_features` per language is a three-line function beside `rle_decode`.
- **`backend-pool.md` rows still say "serving all three".** That file had uncommitted work from the
  `lab-intel3090` bring-up, so this session left it alone rather than mixing two changes; the rows
  for `lab-3090` and (when it gets the containers) the others should say five.
