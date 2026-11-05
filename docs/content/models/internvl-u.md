# `internvl-u`

InternVL-U — a 4B unified multimodal model from Shanghai AI Lab that **understands a picture, answers
in words, and can produce a new one**. It is the one model here that does not answer with geometry: no
mask, no box, no count.

## At a glance

| field | value |
|---|---|
| Task | image understanding, generation and editing |
| Input | a dataset image or upload, plus `prompt`; `mode` and `size` choose the answer |
| Output | prose in `answer`, a picture in `image`, or both |
| Runs at | 512 / 768 / 1024 px on the longer side; one megapixel at most |
| Typical wait | 0.67 s for prose; 11–15 s for a picture |
| License | [MIT](#references) |

**[Try it on the live demo](https://api.global-wheat.com/play?model=internvl-u)** — the model is
already selected and three example calls are one click away. The `results` eye shows the picture the
model produced; closing it brings the photograph back, and the words appear in the bar under the
picture.

## When to use it

**Good at**

- Describing what is in a picture, in a sentence or two.
- Comparing or interpreting a scene: whether a spike looks diseased, what changed between photographs.
- Making a picture from a prompt, or editing one you send at the level of the whole frame.

**Not for**

- **Counting or measuring.** The language model inside is 1.7B; a count it gives you is a guess in
  prose. Use [`tasselnetv4`](tasselnetv4.md) for a count and [`sam3.1-meta`](sam3.1-meta.md) or
  [`gwfss-segformer-b4`](gwfss-segformer-b4.md) for masks.
- **Fixing one detail.** "Colder evening light" or "a botanical illustration of this" returns a
  plausible new frame; a request to fix one awn is beyond one megapixel and four billion parameters.
- **A fast, cheap picture.** One prose answer holds the GPU for under a second; one picture for 8–15 s,
  and it takes one of your three inferences.

**Limits**

- `prompt` is at most 300 characters and is required in every mode.
- `size` is ignored by `text`; above 1024 it is clamped and the answer says so in `warnings`.
- The picture is generated at **one megapixel at most**; a larger edit is re-run at the ceiling.

## Call it

With the client from [Clients](../api/clients/index.md) set up, or the `API` and `KEY` variables from [Raw HTTP](../api/http/index.md#five-calls):

=== "Python"

    ```python
    answer = client.predict(
        "internvl-u", "gwhs-temp", 0,
        prompt="Describe this image.", mode="text",
    )
    ```

=== "JavaScript"

    ```js
    const answer = await client.predict("internvl-u", "gwhs-temp", 0, {
      prompt: "Describe this image.",
      mode: "text",
    });
    ```

=== "Java"

    ```java
    Map<String, Object> answer = client.predict(
        "internvl-u", "gwhs-temp", 0,
        Map.of("prompt", "Describe this image.", "mode", "text"));
    ```

=== "HTTP"

    ```bash
    curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
      -d '{"dataset": "gwhs-temp", "row": 0}' \
      "$API/models/internvl-u/predict?prompt=Describe%20this%20image.&mode=text"
    ```

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


## Using it

### Modes

- **`text`** — a sentence or two about the picture you sent.
- **`image`** — a picture made from your `prompt`, or an edit of the picture you sent.
- **`text_image`** — the model reasons first, then produces the picture.

### Editing

Send a photograph with `mode="image"` and a prompt that describes the change. The model edits at the
level of the frame, not the detail: it redraws the whole picture, so the answer lines up with yours
while its pixels are new.

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
is about 1 MB of PNG, in a body of about 1.4 MB.


## Performance

### Quality

It describes; it does not measure. Its value is a sentence about what is visible, or a new frame that
follows the instruction, not a number you can count on.

### Speed

On a single RTX 4090, bfloat16, one request at a time:

| mode | latency | what it produced |
|---|---|---|
| `text`, 1024 × 1024 input | 0.67 s | 141 characters of prose |
| `image`, 1024² generated | 11.2 s | 1.0 MB PNG |
| `text_image`, 1024² generated | 11.7 s | prose and the same frame |
| `image`, 3456 × 5184 edit → 768 × 1152 | 15.3 s | 0.9 MB PNG |

The wait depends on what you ask for: prose is sub-second, a generated picture is around 11–12 s, and
an edit of a large upload is around 15 s.

## References

- **Paper** — Tian et al., “InternVL-U: Democratizing Unified Multimodal Models for Understanding,
  Reasoning, Generation and Editing,” arXiv 2026. [arXiv:2603.09877](https://arxiv.org/abs/2603.09877)
- **Code** — [OpenGVLab/InternVL-U](https://github.com/OpenGVLab/InternVL-U)
- **Weights** — [InternVL-U/InternVL-U](https://huggingface.co/InternVL-U/InternVL-U)
- **License** — [MIT](https://github.com/OpenGVLab/InternVL-U/blob/main/LICENSE), Copyright (c) 2026
  OpenGVLab
