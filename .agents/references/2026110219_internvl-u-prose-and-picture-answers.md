# InternVL-U: what one answer costs, and what it takes to serve it

Date: 2026-11-02 19:00 JST. Branch: `main`. Measured on `homepc-4090` (RTX 4090, 24 GB, driver
615.71.09), worked in `models/internvlu/`, `gateway/src/biohack_gateway/models.py` and the three
clients.

This is the second capability the contract has gained that is not geometry. The first was a
backbone's tokens; this one is prose and pixels, and the question the session started with was
whether the pool could carry it at all. It can, and the numbers below are why.

## What the model is

A 4B unified multimodal model from Shanghai AI Lab, MIT, published 2026-03. It is not one network: an
InternViT vision tower and a **Qwen3-1.7B** language model answer questions, and a 20-layer MMDiT
head over a Qwen-Image VAE produces pictures. Three payloads, 8.35 GB in bfloat16 — `vlm` 4.70 GB,
`generation_decoder` 3.81 GB, `vae` 0.25 GB.

That split is the useful fact about it. Prose is the cheap half, pictures are the expensive one, and
`generation_mode="text"` never touches the decoder.

## Measured

One process per mode, the pinned stack (`torch 2.6.0+cu124`, `transformers 4.52.3`,
`diffusers 0.35.1`), no flash-attn — the pipeline chooses an SDPA processor when it is absent, so the
wheel that takes forty minutes to build is not needed for either path.

| mode | input | latency | process peak | resident after load |
|---|---|---|---|---|
| `text` | 1024×1024 | **0.67 s** | 8.9 GiB | 8.47 GiB |
| `image` (t2i) | none, 1024² | **11.2 s** | 13.4 GiB | 8.47 GiB |
| `text_image` | none, 1024² | **11.7 s** | 13.9 GiB | 8.47 GiB |
| `image` (edit) | 3456×5184 → 768×1152 | **15.3 s** | 13.6 GiB | 8.47 GiB |
| `image` (edit, unbounded) | 3456×5184 | **OOM** | — | — |

A 1024² answer is a **1.0 MB PNG**, which is the number the 15 MB question actually turned on: base64
inflates it to 1.34 MB, an order of magnitude below Caddy's upload cap and the gateway's 16 MiB
`MAX_RESPONSE_BYTES`.

Quality, informally: a prompt for a wheat spike against soil at midday returned a plausible spike with
awns and visible glumes in 11 s. Editing the panda-and-boy fixture for evening light returned the same
subjects relit — and restyled toward a render, which is what "content-level edit" means here and why
the answer's `resolution_clamped` warning matters more than its quality.

## What broke

- **The edit path has no ceiling of its own.** The README's example passes
  `height=input_image.size[1], width=input_image.size[0]`, so a 3456×5184 dataset frame asks the
  decoder for 17.9 megapixels. It dies inside `repeat_interleave` on a 3.71 GiB allocation, on a card
  with 20 GB free, after the model has already been paid for. The bound is therefore imposed before
  the model runs (`outputs.fit`, one megapixel, sides aligned to the VAE's 16-pixel token), and the
  answer says it did so.
- **`pipe.generation_decoder = None` does not free the decoder.** The card dropped 128 MiB after
  `del` and `empty_cache`, against 3.8 GB of weights. A prose-only container cannot be made cheap by
  dropping a component after loading; it would need not to load it.
- **`pkill -f internvlu-probe/…` over SSH kills the session.** The pattern matches the remote shell's
  own command line. Kill by port or pidfile instead.
- **Manjaro's system Python is 3.14.** `uv venv --python 3.12` fetches a managed interpreter; nothing
  else in the probe environment is unusual.

## What this changes in the contract

`ModelPrediction` gained `answer: str | None` and `image: GeneratedImage | None`. Both are additive
and `objects` stays required and empty, which is the same shape a counting model and a backbone
already send, so one client still parses every model. The gateway relays the picture byte for byte and
decodes nothing: the container decides the format.

The load-time peak is the reason the container declares **8.7 GB resident and 5.2 GB per request**,
not the 13.9 GB total twice over: the first figure is what a card must have to hold the process, the
second what one picture adds, and the admission is their sum.

## Deployed, 2026-11-03

The image was built on `homepc-4090` from `models/internvlu/` — 16 steps, the three payloads fetched
inside the build and checked by byte count — and runs there as `internvl-u` on host port `12716`,
`INTERNVLU_MACHINE=homepc-4090`, its token read from `~/.config/biohack-backend.env` rather than typed
on the command line. `models.json` on the gateway host gained the entry and the service was
restarted; `GET /models` reports `internvl-u` with one backend, and a text call on `gwfss` row 4
answered in 1.0 s.

The same image is in ECR so the rented path can pull it:

| | |
|---|---|
| repository | `144931731874.dkr.ecr.eu-central-1.amazonaws.com/internvl-u` |
| tag | `20261103` (`IMMUTABLE`, scan on push) |
| digest | `sha256:b8cdcb03d6c7f08009d872a0446e3bca0930977109800420b4661cc727dccbba` |
| compressed | 14.0 GB |

Pushed from the machine that built it, 17 layers, about 25 minutes at 10 MB/s upstream. The digest is
the identity a launch template pins; the tag is only for humans.

## What is not done

- **No AWS instance runs it.** The image is in ECR and the launch template still names another model's
  digest; nothing was launched, so `aws-euc1-l4` remains a base AMI and a recipe.
- **The Blackwell port is not started.** The pinned stack is cu124; B100 is sm_100 and the 5090 is
  sm_120, and neither has kernels in those wheels. `models/internvlu/Dockerfile` already sits on a
  12.8.1 base so the move is a `uv lock` and a re-test, in that order, on a 3090 or 4090 first.
- **The docs site has no page for it**, and `fixtures/model-directory.json` is not refreshed — both
  need a live `GET /models`, which needs a running backend.
- **The `/play` page has no picture pane.** It previews an input and draws masks; a returned picture
  has nowhere to go yet.
- **`text_image` reasoning is not surfaced separately.** The prose is the answer, and the frame is
  beside it; the model's own chain of thought is not published, because no caller has asked for it.
