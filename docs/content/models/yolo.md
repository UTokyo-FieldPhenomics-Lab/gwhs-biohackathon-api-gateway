# YOLO26

<!-- models: yolo26-ultralytics yolo26-seg-ultralytics -->

Two checkpoints from [Ultralytics](https://docs.ultralytics.com/models/yolo26/), served as one
family: **`yolo26-ultralytics`** answers boxes, and **`yolo26-seg-ultralytics`** answers the same
boxes plus a mask per object. Both are the COCO models as Ultralytics publishes them, and they are
the fastest models this API serves — one call is a few milliseconds on the cards behind it.

**[Try v26](https://api.global-wheat.com/play?model=yolo26-ultralytics)** — the picker groups the two
under **YOLO**, and the second control chooses the one with the mask head.

*Served under the [GNU Affero General Public License v3.0](https://github.com/ultralytics/ultralytics/blob/main/LICENSE),
which is the licence of both the code and the weights. The model code is Ultralytics'; the images
behind this service are private to it, and the weights are not distributed through this API.*

## The two ids

| | `yolo26-ultralytics` | `yolo26-seg-ultralytics` |
|---|---|---|
| one object is | a box and a class | a box, a class and a mask, as COCO RLE |
| trained on | COCO's 80 classes | the same 80 classes, with instance masks |
| an upload may be | 50 megapixels | 4 megapixels, and at most 100 objects back |
| one 1024² call | 7.1 ms | 9.3 ms |

The upload ceiling is different on purpose. A box costs the same whatever the picture's size; a mask
is an array the size of the picture **per object**, so the mask answer admits a smaller picture and a
bounded number of objects. A larger upload is refused with a `400` naming the limit rather than
answered slowly — downscale it and send it again.

## The three example photographs

COCO's classes are not in `gwhd`, so the playground's chips for this family do something the others
do not: they upload a picture instead of naming a dataset row. The pictures are chosen for classes
these checkpoints actually know — **carrots**, **broccoli** and **a potted plant**. They live under
the data distribution's `dev/` prefix, and the playground asks this API for them, which reads the
object once and answers it from here — a browser cannot read another origin's bytes to upload them:

| example | picture | licence |
|---|---|---|
| carrots | [`dev/carrot.jpg`](https://data.global-wheat.com/dev/carrot.jpg) | public domain, Wikimedia Commons |
| broccoli | [`dev/broccoli.jpg`](https://data.global-wheat.com/dev/broccoli.jpg) | CC0, Wikimedia Commons |
| a potted plant | [`dev/potted-plant.jpg`](https://data.global-wheat.com/dev/potted-plant.jpg) | CC0, Wikimedia Commons |

They are a demonstration and nothing else: the same call on a `gwhd` row answers an empty list, and
that is the honest answer from a model that has never seen a wheat head.

## Parameters

Both ids answer the same two parameters, and they ride in the **query string** beside the address:

### `yolo26-ultralytics`

<!-- parameters:yolo26-ultralytics:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `confidence_threshold` | `0.25` | `0` – `1` | How sure the model must be before it reports an object. |
| `imgsz` | `1280` | `640`, `1024`, `1280`; a larger value runs at `1280` | The square side the network runs at. A smaller one is faster and worse on small objects; a larger one runs at the ceiling and says so in `warnings`. |
<!-- parameters:yolo26-ultralytics:end -->

### `yolo26-seg-ultralytics`

<!-- parameters:yolo26-seg-ultralytics:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `confidence_threshold` | `0.25` | `0` – `1` | How sure the model must be before it reports an object. |
| `imgsz` | `1280` | `640`, `1024`, `1280`; a larger value runs at `1280` | The square side the network runs at. A smaller one is faster and worse on small objects; a larger one runs at the ceiling and says so in `warnings`. |
<!-- parameters:yolo26-seg-ultralytics:end -->

`imgsz` is the square side the network runs at, and the three values are the ones this deployment
measured. A larger value is not an error: it runs at `1280` and says so in `warnings`. A smaller one
is faster and worse on small objects.

`confidence_threshold` is the same name and the same bounds every object-answering model here
publishes, so a caller that lowers it for `sam3.1-meta` lowers the same knob here.

## What comes back

`yolo26-ultralytics` answers one object per detection: `category`, `score` and `bbox` in the
picture's own pixels. `yolo26-seg-ultralytics` answers the same objects and adds a COCO `rle` mask to
each. `objects` is empty when the picture holds nothing the checkpoint knows — on a `gwhd` row, that
is both ids' answer. The rest of the envelope is the shared one: `resolution_used` and `computed_by`.

## What it is not

**It is not trained on wheat.** COCO's 80 classes do not include a wheat head, and a `gwhd` row
answers zero objects from both ids — not a low score, an empty list. That is the honest answer from
a general detector, and it is why the pool also serves `sam3.1-meta`, which is given the words for
what to look for. When a fine-tune trained on wheat exists it will appear as an id of its own, named
after the data it learned from, beside these two in the same picker.

**The mask head is instance segmentation, not the organ map.** `gwfss-segformer-b4` answers one
extent per class — every head pixel, every stem pixel — because that is what the dataset annotates.
`yolo26-seg-ultralytics` answers separate objects, each with its own mask, because that is what an
instance detector is. On a wheat picture with no wheat-trained weights it finds nothing at all.

## Measured on the RTX 3090 behind this API

Through the containers' own code, on `gwhd` rows, ten timed calls each after a warm-up pass:

| `imgsz` | `yolo26-ultralytics` | `yolo26-seg-ultralytics` |
|---|---|---|
| `640` | 6.3 ms | 7.7 ms |
| `1024` | 7.1 ms | 9.3 ms |
| `1280` | 12.1 ms | 15.1 ms |

The detection image holds about 176 MiB of the card with its weights loaded; the mask image about
48 MiB, and its worst admitted request — 4 megapixels, 100 objects — peaks near 506 MiB. Both are
small enough that the pair shares a card with everything else the pool serves.

## When to use your own

The checkpoints are public and Ultralytics' own, so a fine-tune of your own is a path on your machine
rather than an id to ask us for — and the API serves only the checkpoints it publishes. The
[datasets](../datasets/gwhd.md) are public, the annotation format is
[one decoder](../api/clients/index.md) in all three clients, and `GLOBAL_WHEAT_API` points a client at a
host of your own.
