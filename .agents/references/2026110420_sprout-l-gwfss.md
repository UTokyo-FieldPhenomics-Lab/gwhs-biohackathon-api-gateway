# SPROUT-L on GWFSS: what the pretrained file is, and what the delivered head does

2026-11-04. Branch `main`. Worked in `models/sprout/gwfss/` (the private submodule), the host's
`models.json`, and `docs/`. The constraints are in [`rules/models.md`](../rules/models.md) and
[`rules/gateway.md`](../rules/gateway.md); this is the record of what was measured on the way.

## The question, and the answer that ended it

The model was proposed for four tasks — organ segmentation, crop–weed parsing, monocular depth and
yield-organ counting — from the pretrained checkpoint alone, with no fine-tuning. The pretrained
weights were downloaded and read before anything was built:

| fact | value |
|---|---|
| file | `SPORUT-L_step=700k.ckpt` (the author's spelling), 5,780,543,477 B |
| it is | a Lightning checkpoint: `optimizer_states`, `lr_schedulers`, `callbacks`, `state_dict` |
| `state_dict` | 1,679 tensors, 722.5 M parameters — the model and its EMA, 361.3 M each |
| `model.diffusion_model.*` | 832 tensors |
| output layer | `out.2.weight` `[3, 128, 3, 3]`, `out.2.bias` `[3]` |
| noise schedule | the DDPM buffers travel with it; `alphas_cumprod[50] = 0.984626` |

Loading it into the segmentation configuration with `num_classes=4`, the way `train_segmentation.py`
does, leaves **exactly two tensors unset**: `out.2.weight` and `out.2.bias`. Nothing else is missing
and nothing is unexpected. The pretrained file is the denoiser, and the class head does not exist in
it.

Three random initialisations of that head, scored against four published rows with the paper's own
multi-scale protocol:

| initialisation | mIoU on four rows |
|---|---|
| seed 0 | 7.7 |
| seed 1 | 2.9 |
| seed 2 | 15.8 |
| predicting background everywhere | 14.6 |

A fresh head never beat the constant predictor. And it is not noise: it draws a fine speckle that
follows the organ boundaries of the photograph, which is the failure that would have shipped — a demo
that looks like a segmentation and means nothing. The counting configuration is worse than useless in
the same way: its entire head is 1,537 parameters and none of them are trained, so an all-black image
answers **68.4** spikes while a real canopy answers 41–69 depending on the seed.

Two further facts from the same afternoon, both worth keeping:

- **The pretrain has nothing to say about a clean image.** Handed a GWFSS row at `t=50`, its
  three-channel output is flat noise — the correct answer for a denoiser told to remove noise from a
  clean picture. `x0` at that timestep is the input itself, because `alphas_cumprod[50] = 0.9846`.
  There is no "reconstruction" mode to expose.
- **Its features do carry structure.** The bottleneck emits a 16×16×512 grid and a 768-dimensional
  CLS vector, and a PCA of the grid separates soil from plants. That is the shape DINOv3 answers in,
  and it is the only honest zero-training use of the pretrained file.

Cost, on the laboratory 3090: one 256×256 forward is 69 ms and 1.79 GiB; the paper's own protocol —
three scales, stride 128, flip TTA — is 50 windows and 100 forwards, **5.7 s** for one 512×512 row
against the gateway's 10 s response budget.

## The head that arrived

The author delivered `gwfss_inference.zip` into the private weight store — file id
`1sLucwxQTZTLkrUdvOdMOEYsPRtyuHV5r`, 1,340,316,115 B — holding `best.ckpt` (1,445,635,387 B), her
model code, and an fp16 benchmark, Apache-2.0. Her recorded evaluation: **78.80 % mIoU on 99 validation
images and 75.10 % on 110 test images**, trained on 884 (competition train plus unused, after removing
three duplicates with validation).

The checkpoint differs from the pretrained one in **one structural tensor**: `out.2` goes from
`[3,128,3,3]` to `[4,128,1,1]`. Every other tensor keeps its name and shape, and every one of them
moved:

| stage | ‖Δ‖/‖w‖ against pretrain | parameters |
|---|---|---|
| input_blocks (encoder) | 1.67 % | 58.7 M |
| transformer (DiT) | 1.30 % | 129.1 M |
| output_blocks (decoder) | 1.85 % | 172.8 M |
| `out.2` (class head) | shape changed | 516 |

So the served model is 98.5 % the pretrained network plus a trained 1×1 convolution. That is the
precise version of "the tasks are post-processing of the feature map": the post-processing is one
convolution, and it is the part that must be trained.

## What was built

`models/sprout/gwfss/` — one folder, one container, one id. `serve.py` and `outputs.py` are the
sibling semantic container's with the environment prefix renamed; `infer.py` is rewritten and runs her
validated protocol exactly (512 square, four 256 windows at stride 256, `t=50`, `scale=2.0209`). The
model code is vendored unchanged, and `fetch_weights.py` asserts the archive's byte count and then the
checkpoint's.

| | |
|---|---|
| id | `sprout-l-gwfss`, family `SPROUT`, label `v1-seg-L`, port `12719` |
| answer shape | unchanged from `gwfss-segformer-b4`: one object per class, COCO RLE |
| mIoU, ten published rows | **0.7275** (background .761, head .885, stem .432, leaf .832), against the SegFormer's 0.701 on the same rows |
| the same ten rows through `POST /predict`, RLE decoded by the participant client | **0.7275** — the encoder and the contract's decoder agree |
| latency | 287 ms in-process, 318 ms through the gateway including the image fetch |
| VRAM | 2740 MiB |

The two mIoU figures being identical is the check that mattered: it says the RLE round-trips and that
the container's numbers are the folder's numbers.

## Deployment

`models.json` on `gwhs-host` gained one entry and the gateway was restarted; `/models` reports
`backends_up: 1`, and a `predict` on `gwfss` row 0 answered in 1164 ms cold and 3 ms cached. The
`/play` preset is one line — `{geometry: false, legend: true}` — and the landing model did not move.

## Lessons

- **A pretrained denoiser is not a task model.** Four task heads were asked for; the file carries
  none. Reading the checkpoint took ten minutes and should have preceded any deployment plan.
- **A randomly initialised head is worse than an empty one**, because it produces a plausible picture.
  Any future "does this checkpoint work" question should be answered against a constant-predictor
  baseline, not against zero.
- **The square resize is the part that breaks non-square inputs**, and it is invisible on `gwfss` and
  `gwhd` because both are square. `gwhs` rows are portrait (247–1601 × 547–4263, mean aspect 0.53),
  and stretching one to 512² is a second reason — beside the desk-versus-field domain gap — that the
  model answers them badly.
- **Re-record `model-directory.json` by merging, never by replacing.** A fresh `GET /models` recorded
  while `internvl-u`'s backend was down turned that model's declared parameter table into
  "no backend has declared this model yet". `make_models.py` would have committed the regression.
