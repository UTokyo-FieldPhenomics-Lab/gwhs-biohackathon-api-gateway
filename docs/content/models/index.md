# Models

Two models answer today, and calling either is one request: **you send an image address (or your own
photograph) and you get masks back**. One finds what you describe in words; the other labels every
pixel of a wheat canopy as head, stem or leaf. No weights travel to you, nothing runs on your machine,
and there is no environment to set up.

## What runs it

The API routes inference to GPUs we run ourselves, and you can use them without any setup of your own:

| machine | institution | card | VRAM | notes |
|---|---|---|---|---|
| `lab-5090` | UTokyo Field Phenomics Lab | RTX 5090 | 32 GB | the largest card; a Windows host |
| `lab-3090` | UTokyo Field Phenomics Lab | RTX 3090 | 24 GB | measured 0.36 s per inference; 3.7 GB resident + 1.8 GB per request |
| `homepc-4090` | personal support, offered for the event | RTX 4090 | 24 GB | a lab member's own workstation |

More cards are being added before the event. `GET /models` is the live answer — `backends_up` is how
many machines answered the gateway's last probe, and `checked_seconds_ago` is how long ago that was, so
it is a verdict with an age rather than a promise. `0` means a call is refused rather than queued.
[The live demo](../examples/demo.md) shows the same two numbers above its run button.

Two things are fixed by the pool rather than by the request:

- **Each model runs at a side it was measured at.** `sam3.1-meta` runs at one of `672`, `784`, `896`
  or `1008` pixels square, 1008 the default and the ceiling; `gwfss-segformer-b4` runs at the `512`
  it was trained at and offers no choice at all. Your image is resized to that square *without
  preserving aspect ratio*, and the answer reports `resolution_used` so you can tell how coarse a
  mask is at full size. A larger `imgsz` is not an error: it runs at the ceiling and says so in
  `warnings`. A smaller one is faster and worse on small objects — the table on the model page has
  the numbers. No model accepts a resolution it was not run at.
- **Three inferences in flight per key, no queue.** Over it, `429`. The three are three across the
  whole team, not three per person, and only the predict routes take one — a dataset listing or the
  model directory is answered from memory and never queues behind a GPU; see
  [Limits](../api/http.md#limits).

**What is not a parameter, and why.** `device`, `half` and any per-request precision or memory switch
describe **the machine**, not the model: they belong to the container's deployment, and a caller who
could set them would decide how much of a card one request takes — which is the number the pool
admits against. A knob is exposed only when it cannot raise that figure. `imgsz` passes that test
downwards only, which is why it has a ceiling rather than a range, and why a value above it is clamped
instead of honoured.

## The models

| id | what it is | runs at | measured |
|---|---|---|---|
| [`sam3.1-meta`](sam3.1-meta.md) | SAM 3.1 — text-prompted segmentation, [Meta's own code and checkpoint](https://github.com/facebookresearch/sam3) | 672 / 784 / 896 / 1008 px square | 0.36 s per inference on a 3090 at 1008; parameters and their encodings on the model page |
| [`gwfss-segformer-b4`](gwfss-segformer-b4.md) | the GWFSS dataset's own SegFormer-B4 baseline — semantic organ segmentation, [CC-BY-4.0 weights](https://huggingface.co/GlobalWheat/GWFSS_Segformer_b4), no parameters | 512 px square | 0.038 s per inference on a 3090; mIoU 0.70 on the ten published rows, stems the weak class; numbers on the model page |

An id names **whose weights these are**, not where they run: a collaborator's fine-tune of the same
model appears as an id of its own, named after the data it was trained on. Every id this API serves is
something it answers — a model with no machine behind it answers `0` and a call is refused rather than
queued.

## When to use your own

The API is here so that nobody has to own a GPU for the event, not because it is the only way. Run it
yourself when:

- **You are processing thousands of rows.** Each call is a round trip; a loop over a whole dataset is
  faster on a card you hold. The datasets are public and so are the model weights.
- **You are fine-tuning.** This service only runs inference, and only the checkpoints it publishes.
- **You need a different checkpoint.** A fine-tune of your own is a model id we would have to host; on
  your own machine it is a path.
- **You need to work without a key**, or you want results to be reproducible on your own hardware.

Everything on this site still applies: the datasets and the annotation formats are the same, and the
clients speak to any host — `GLOBAL_WHEAT_API` in the environment points a client at yours.
