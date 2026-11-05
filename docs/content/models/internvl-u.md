# `internvl-u`

InternVL-U — a 4B unified multimodal model from Shanghai AI Lab that **understands a picture, answers
in words, and can produce a new one**. It is the one model here that does not answer with geometry: no
mask, no box, no count.

Three modes, chosen with `mode`:

- **`text`** — a sentence or two about the picture you sent.
- **`image`** — a picture made from your `prompt`, or an edit of the picture you sent.
- **`text_image`** — the model reasons first, then produces the picture.

**[Try it on the live demo](https://api.global-wheat.com/play?model=internvl-u)** — the model is
already selected and three example calls are one click away. The `results` eye shows the picture the
model produced; closing it brings the photograph back, and the words appear in the bar under the
picture.

## Parameters

<!-- parameters:start -->
| parameter | default | accepts | what it does |
|---|---|---|---|
| `prompt` | — | text, at most 300 characters | The question to answer, or the instruction describing the picture to produce. Required: this model has no default prompt. |
| `mode` | `text` | `text`, `image`, `text_image` | `text` answers in prose, `image` produces a picture from the prompt, and `text_image` reasons first and then produces one. `image` and `text_image` on an uploaded image edit it rather than generate a new one. |
| `size` | `1024` | `512`, `768`, `1024`; a larger value runs at `1024` | Longer side of the picture the model generates, in pixels; the shorter side follows the input's aspect ratio. These are the sizes this image was measured at. Ignored by `text`, whose answer is prose. |
<!-- parameters:end -->

`prompt` is **required** in every mode. `size` is ignored by `text`, whose answer is prose, and is one
of three sides this container was measured at otherwise; above the largest it is clamped and the
answer says so in `warnings`.

The picture is generated at **one megapixel at most**, and an edit is no exception: a 3456 × 5184
upload is re-run at the ceiling and comes back at about 1024 px on its longer side. The answer always
reports the size it actually produced in `image.width` and `image.height`, and a reduced edit carries
`{"code": "resolution_clamped"}`.

## What comes back

`objects` is present and empty, so one client parses this answer and a segmenter's with the same code.
The prose arrives in `answer`, the picture in `image`:

```json
{
  "model": "internvl-u",
  "objects": [],
  "answer": "A close-up of dried wheat stalks with golden, feathery heads, growing among dark, tangled stems.",
  "image": {
    "media_type": "image/png",
    "width": 768,
    "height": 768,
    "data": "iVBORw0KGgoAAAANSUhEUgAA…"
  },
  "resolution_used": 768,
  "computed_by": {"machine": "homepc-4090", "gpu": "NVIDIA GeForce RTX 4090"}
}
```

`image.data` is base64 of the encoded file, exactly as the container wrote it. The clients decode it
for you — `image["picture"]` is a Pillow image in Python, the file's bytes in JavaScript and Java — and
nothing on this service re-encodes it, so what you save is what the model produced. A 1024 px answer
is about 1 MB of PNG, in a body of about 1.4 MB, well inside the 15 MB an upload may be.

## What it is and is not for

**It describes; it does not measure.** Ask it what is in a picture, whether a spike looks diseased, or
what changed between two photographs. Do not ask it how many heads there are: the language model
inside is 1.7B and a count it gives you is a guess in prose. `tasselnetv4` answers a number with the
field that number was summed from, and `sam3.1-meta` or `gwfss-segformer-b4` answer masks.

**It edits at the level of the frame, not the detail.** An instruction like "colder evening light" or
"a botanical illustration of this" returns a plausible new frame; a request to fix one awn is beyond
what one megapixel and a four-billion-parameter model will give you.

**It costs a card.** One prose answer holds the GPU for 0.7 s; one picture for 8–15 s. That is why it
is its own id rather than a mode on a task model, and why it takes one of your three inferences like
everything else.

## Measured

On a single RTX 4090, bfloat16, one request at a time:

| mode | latency | what it produced |
|---|---|---|
| `text`, 1024 × 1024 input | 0.67 s | 141 characters of prose |
| `image`, 1024² generated | 11.2 s | 1.0 MB PNG |
| `text_image`, 1024² generated | 11.7 s | prose and the same frame |
| `image`, 3456 × 5184 edit → 768 × 1152 | 15.3 s | 0.9 MB PNG |

The container holds 8.5 GiB resident and peaks at 13.9 GiB during a picture, which is why it runs on a
card of its own rather than beside the segmentation models.

## Licence

InternVL-U is served under the **MIT License**, Copyright (c) 2026 OpenGVLab. The copyright notice and
this permission notice are reproduced with it:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or substantial
> portions of the Software.

The checkpoint is [InternVL-U/InternVL-U](https://huggingface.co/InternVL-U/InternVL-U); the report is
[arXiv:2603.09877](https://arxiv.org/abs/2603.09877).
