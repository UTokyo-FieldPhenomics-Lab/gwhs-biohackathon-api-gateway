# SAM 3.1: an empty answer is a decision, and a box is an example

Reported on 2026-11-02 from `/play`: `gwhd` row 4 with `prompt=wheat head` and no boxes answers nothing
at any `confidence_threshold`, and drawing one box around a head makes 22 heads appear all over the
frame. Both facts are the model working as built; the model page was describing them wrongly, and the
playground said nothing at the moment of confusion. This note records the measurement, the code paths
that explain it, and the two edits that followed.

## What the code does

Two lines decide everything below.

- `vendor/sam3/model/sam3_image_processor.py`, `_forward_grounding`: `out_probs = out_logits.sigmoid()`
  is multiplied by `presence_logit_dec.sigmoid()` **before** the threshold is applied. The presence
  score belongs to the whole prompt, not to one object, so a low one pulls every object down together.
- `vendor/sam3/model/sam3_image.py`, `_encode_prompt`: `prompt = torch.cat([txt_feats, geo_feats])` —
  the text tokens and the box/point tokens are one prompt, attended over the whole image by 200
  decoder queries (`model_builder.py:185`). Nothing is cropped, so nothing is an ROI.

`serve.py` runs text first, then each box, then the points, one forward each; `processor.py` adds the
point path upstream does not expose. None of that changes the two facts above.

## How this was measured

Against the container already serving on the gateway host, on 2026-11-02:

```sh
# the token the backend already runs with, never printed
export TOKEN=$(docker inspect sam3.1-meta --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^BACKEND_TOKEN=//p')

# one call: POST /models/sam3.1-meta/predict?<params> with an image address
#   body    {"input": {"url": "https://data.global-wheat.com/gwhd/images/00000004.jpg"}}
#   header  X-Backend-Token: $TOKEN
```

`gwhd` row 4 is 1024 × 1024; the reported box `[[459,297,86,154,1]]` does sit on a head. Everything
below is at `imgsz` 1008 unless it says otherwise.

## Results

**The threshold is not the lever.** `prompt=wheat head`, `gwhd` row 4:

| `confidence_threshold` | objects | highest score |
|---|---|---|
| `0.31` | 0 | — |
| `0.10` | 0 | — |
| `0.0` | **200** | `0.0063` |

200 is every query the decoder has, kept because they are all greater than zero, at a score that is
noise rather than heads. Nothing between 0 and 0.31 is a filter; it is a presence gate.

**The words are the lever.** `gwhd`, `confidence_threshold` 0.31, text only:

| prompt | row 0 | row 4 | row 7 |
|---|---|---|---|
| `wheat head` | 0 | 0 | 0 |
| `seed head` | 0 | 44 | 6 |
| `spikelet` | 18 | 51 | 10 |
| `ear of wheat` | 0 | 0 | 4 |
| `wheat` | 0 | 0 | 6 |

`gwfss` row 4 still reproduces the page's baseline exactly — `wheat head` at 0.25 gives 18 objects,
`Σ mask = Σ area = 109,419 px` — so the phrase is image- and wording-dependent, not a broken default.

**A box is an example, not a region.**

| request | objects | highest score | where the top object is |
|---|---|---|---|
| box on a head, no text | 65 | 0.941 | the box itself |
| box on bare soil, no text | 2 | 0.734 | the soil patch |
| `wheat head` + box on a head | 22 | 0.949 | the box itself, then heads across the frame |
| `seed head` + box on a head | 62 | 0.938 | the box itself |
| `spikelet` + box on a head | 70 | 0.941 | the box itself |
| one point on a head, no text | 63 | 0.766 | — |
| `wheat head` + that point | 0 | `0.293` at threshold 0 | — |
| a negative box alone | 0 | — | — |

Two readings worth keeping: a box on soil returns soil, which is what "match this appearance
everywhere" means; and the same point that answers 63 objects alone answers none beside the phrase,
because the phrase's presence pulls the whole answer down. Text and geometry are not one filter plus
one region.

## What changed

- `docs/content/models/sam3.1-meta.md`: the opening paragraph no longer claims the checkpoint cannot
  see dense small heads — `gwhd` row 4 is a close-up and `spikelet` finds 51 there. Prompting now says
  a box is an example and not a region, cross-links `tasselnetv4`'s exemplar boxes, and the new
  "What the score means" section carries the multiplication, the 0-versus-200 measurement and the
  wording table.
- `gateway/src/biohack_gateway/assets/play.js`: an empty answer with no warning now says
  `0 objects · try another phrase, or draw an example box`, built from the declared parameters, so a
  model with neither says nothing and a counting answer keeps its blank. Covered by
  `gateway/tests/test_app.py::test_an_empty_answer_names_the_levers_the_model_declares`.

## What is not done

- **The container's own parameter text is unchanged.** `confidence_threshold` still reads "How sure
  the model must be before it reports an object" in `GET /models`, the site's table and the page
  tooltip, which is incomplete for a model that multiplies by a prompt-level score. Correcting it
  means rebuilding the image and re-recording `docs/scripts/fixtures/model-directory.json`; the model
  page now says the missing half, so the trigger to return is a second participant misreading it.
- **The presence score is not exposed.** Publishing it would separate "not in the picture" from "not
  sure about this object" in a machine-readable way, but it is a contract field only this model can
  fill. Deferred until a caller needs the distinction without reading the docs.
- **No counting answer for `gwhd`.** 51 heads at 0.31 from a phrase is not a defensible field count;
  the trigger recorded in `rules/models.md` for a fine-tune under its own id is unchanged.
