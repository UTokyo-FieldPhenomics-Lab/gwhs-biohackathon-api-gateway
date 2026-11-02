# Models

Five models answer today, and calling any of them is one request: **you send an image address (or
your own photograph) and you get the answer back**. Three answer a task: one finds what you describe
in words, one labels every pixel of a wheat canopy as head, stem or leaf, one counts what you show it
an example of. Two are **backbones**: they answer the encoder's own tokens — a CLS vector and a grid
of patch vectors — and you fit the head your question needs. No weights travel to you, nothing runs on
your machine, and there is no environment to set up.

## The models

| id | what it is | runs at | measured |
|---|---|---|---|
| [`sam3.1-meta`](sam3.1-meta.md) | SAM 3.1 — text-prompted segmentation, [Meta's own code and checkpoint](https://github.com/facebookresearch/sam3) | 672 / 784 / 896 / 1008 px square | 0.36 s per inference on a 3090 at 1008; parameters and their encodings on the model page |
| [`gwfss-segformer-b4`](gwfss-segformer-b4.md) | the GWFSS dataset's own SegFormer-B4 baseline — semantic organ segmentation, [CC-BY-4.0 weights](https://huggingface.co/GlobalWheat/GWFSS_Segformer_b4), no parameters | 512 px square | 0.038 s per inference on a 3090; mIoU 0.70 on the ten published rows, stems the weak class; numbers on the model page |
| [`tasselnetv4`](tasselnetv4.md) | TasselNetV4 — class-agnostic plant counting, [the paper's code and checkpoint](https://arxiv.org/abs/2509.20857); you show exemplar boxes and it answers a number | 384 px tall, tiled | 0.31 s per inference through the API; MAE 8.75 on `salaamuko` (48 images) and 10.8 over 32 sampled species; numbers on the model page |
| [`dinov3-vitl16`](dinov3-vitl16.md) | DINOv3 ViT-L/16 — a **backbone** for dense features and a CLS descriptor, [the LVD-1689M checkpoint](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m); no head, no classes | 224–768 px on the longer side, default 512 | 0.039 s per inference on a 3090 at 512; 2.8 MB answer at the default, 6.3 MB at the ceiling; numbers on the model page |
| [`dinov3-vith16plus`](dinov3-vith16plus.md) | DINOv3 ViT-H+/16 — the same backbone at 1280 dimensions, [the LVD-1689M checkpoint](https://huggingface.co/facebook/dinov3-vith16plus-pretrain-lvd1689m); no head, no classes | 224–640 px on the longer side, default 384 | 0.026 s on a 4:6 frame at 384 on a 3090; 2.0 MB answer at the default, 5.5 MB at the ceiling; numbers on the model page |

An id names **whose weights these are**, not where they run: a collaborator's fine-tune of the same
model appears as an id of its own, named after the data it was trained on. Every id this API serves is
something it answers — a model with no machine behind it answers `0` and a call is refused rather than
queued.

`GET /models` is the live answer for what is running: `backends_up` is how many machines answered the
last probe and `checked_seconds_ago` is how long ago that was, so it is a verdict with an age rather
than a promise, and `0` means a call is refused rather than queued. Every answer also names the
machine that produced it in `computed_by`, and [the live demo](../examples/demo.md) shows both numbers
above its run button.

## What the API fixes for you

Two things come from the service rather than from the request:

- **Each model runs at a side it was measured at.** `sam3.1-meta` runs at one of `672`, `784`, `896`
  or `1008` pixels square, 1008 the default and the ceiling; `gwfss-segformer-b4` runs at the `512`
  it was trained at and offers no choice at all; `tasselnetv4` resizes every picture to `384` px tall
  and tiles it, because that is the training crop. Those task models resize to a square *without
  preserving aspect ratio*. The two backbones are the exception: `side` is the longer side and the
  shorter one follows the aspect ratio, each dimension floored to 16, so a 4:6 photograph at 512
  runs at 336 × 512. The answer reports `resolution_used` so you can tell how coarse a result is at
  full size. A larger value is not an error for a clamped ceiling: the model runs at it and says so
  in `warnings`. A smaller one is faster and worse on small objects — the table on the model page has
  the numbers. A task model accepts only a resolution it was run at; a backbone accepts the range it
  declares.
- **Three inferences in flight per key, no queue.** Over it, `429`. The three are three across the
  whole team, not three per person, and only the predict routes take one — a dataset listing or the
  model directory is answered from memory and never queues behind a GPU; see
  [Limits](../api/http.md#limits).

**What is not a parameter, and why.** `device`, `half` and any per-request precision or memory switch
describe the machine, not the model: they belong to the deployment, and a caller who could set them
would decide how much of a GPU one request takes. A knob is exposed only when it cannot raise that
figure. `imgsz` passes that test downwards only, which is why it has a ceiling rather than a range,
and why a value above it is clamped instead of honoured. A backbone's `side` is the one range: it
changes the grid and the answer's size, and its ceiling is what the container was measured at, so it
cannot raise a request beyond the memory the model was admitted with.

## When to use your own

The API is here so that nobody has to own a GPU for the event, not because it is the only way. Run it
yourself when:

- **You are processing thousands of rows.** Each call is a round trip; a loop over a whole dataset is
  faster on a card you hold. The datasets are public and so are the model weights.
- **You are fine-tuning.** This service only runs inference, and only the checkpoints it publishes.
  The two backbones are the ones to fine-tune; the task models are already trained.
- **You need a different checkpoint.** A fine-tune of your own is a model id we would have to host; on
  your own machine it is a path.
- **You need to work without a key**, or you want results to be reproducible on your own hardware.

Everything on this site still applies: the datasets and the annotation formats are the same, and the
clients speak to any host — `GLOBAL_WHEAT_API` in the environment points a client at yours.
