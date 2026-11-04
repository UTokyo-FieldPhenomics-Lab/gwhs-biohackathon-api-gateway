# Models

Eight models answer today — twelve ids, because DINOv3 is served at two checkpoints, Nano Banana at
three tiers, and YOLO26 with and without a mask head. Calling any of them is one request: **you send
an image address (or your own photograph) and you get the answer back**. Five are general-purpose:
one segments and detects what you describe in words, one answers in prose or pixels, one is a
backbone whose tokens you fit a head to, one makes pictures from a prompt or an edit, and one detects
and segments what it was trained on — COCO's classes, quickly. Three are specialized for wheat and plant
phenotyping: two label every pixel of a wheat canopy as head, stem or leaf — the dataset's own baseline
and the laboratory's foundation model — and one counts what you show it an example of. No weights travel to you, nothing runs on your machine, and there is no
environment to set up.

## The models

### General models

These are upstream checkpoints, not trained on this project's data: they answer about any picture.

| model | what it is | tags | runs at |
|---|---|---|---|
| [`sam3.1-meta`](sam3.1-meta.md) | SAM 3.1 — text-prompted segmentation, [Meta's own code and checkpoint](https://github.com/facebookresearch/sam3) | `segmentation` · `detection` | 672 / 784 / 896 / 1008 px square |
| [`internvl-u`](internvl-u.md) | InternVL-U — a 4B unified multimodal model, [MIT weights](https://huggingface.co/InternVL-U/InternVL-U); answers in prose, or produces and edits a picture | `vision-language` · `text-to-image` · `image-editing` | 1024 px on the longer side at most; `text` takes no size |
| **Nano Banana** — [`nano-banana-2.1`](nano-banana.md) · [`nano-banana-2`](nano-banana.md) · [`nano-banana-pro`](nano-banana.md) | Google's hosted image models, served through Vertex AI: a picture from a prompt, or an edit of one you send | `text-to-image` · `image-editing` | 512 px – 2K on the longer side; 1K and 2K per id |
| **YOLO26** — [`yolo26-ultralytics`](yolo.md) · [`yolo26-seg-ultralytics`](yolo.md) | Ultralytics' YOLO26 at the COCO checkpoints: a fast detector, and the same model with an instance-mask head; nothing trained on wheat | `detection` · `segmentation` | 640 / 1024 / 1280 px square |
| **DINOv3** — [`dinov3-vitl16`](dinov3.md) · [`dinov3-vith16plus`](dinov3.md) | [ViT-L/16](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m) and [ViT-H+/16](https://huggingface.co/facebook/dinov3-vith16plus-pretrain-lvd1689m) — one backbone at two checkpoints for dense features and a CLS descriptor, both LVD-1689M; no head, no classes | `features` | 224–768 px (L) / 224–640 px (H+) on the longer side; defaults 512 / 384 |

**Built with DINOv3.** The two DINOv3 backbones are served under the [DINOv3
License](https://ai.meta.com/resources/models-and-libraries/dinov3-license/), which requires this
notice when the models are made available to a third party.

### Specialized models

These were trained for a wheat or plant task.

| model | what it is | tags | runs at |
|---|---|---|---|
| [`gwfss-segformer-b4`](gwfss-segformer-b4.md) | the GWFSS dataset's own SegFormer-B4 baseline — semantic organ segmentation, [CC-BY-4.0 weights](https://huggingface.co/GlobalWheat/GWFSS_Segformer_b4), no parameters | `segmentation` | 512 px square |
| [`sprout-l-gwfss`](sprout-l-gwfss.md) | SPROUT-L — the laboratory's diffusion foundation model, fine-tuned on GWFSS; semantic organ segmentation at 361 M parameters, [Apache-2.0](https://github.com/UTokyo-FieldPhenomics-Lab/SPROUT). Trained on in-field canopies, so a `gwhs` desk photograph is outside it | `segmentation` | 512 px square |
| [`tasselnetv4`](tasselnetv4.md) | TasselNetV4 — class-agnostic plant counting, [the paper's code and checkpoint](https://arxiv.org/abs/2509.20857); you show exemplar boxes and it answers a number | `counting` | 384 px tall, tiled |

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
  runs at 336 × 512. `internvl-u` generates one megapixel at most, and an edit is no exception: a
  photograph larger than that is re-run at the ceiling and answered smaller, with a warning saying
  so. The answer reports `resolution_used` so you can tell how coarse a result is at full size. A larger value is not an error for a clamped ceiling: the model runs at it and says so
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
