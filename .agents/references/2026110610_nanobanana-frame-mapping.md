# Nano Banana: an edit answers in the picture's frame

A reviewer reported that an edit of a rectangular photograph came back square and could not be laid
over the picture it was meant to change. The cause was ours: `aspect_ratio` defaulted to `1:1` on
`predict` and `upload`, and the prepared reference was sent at its own shape, so the model chose what
to do with the difference. The fix makes the frame this service's decision, records it, and leaves
the answer's bytes to the model.

## The flow

1. The reference is decoded, EXIF-applied and thumbnailed to the declared side, as before.
2. `aspect_ratio=auto` (the new default) takes the supported ratio nearest the picture's own. If the
   picture already has that ratio within 0.5%, nothing more happens. Otherwise the picture is placed
   centred on a canvas at that ratio, with its edge pixels continued into the band; the box it
   occupies is recorded as fractions.
3. The canvas, not the picture, is sent. The upstream is never asked to reframe.
4. The answer is the upstream's own file. It carries `frame` — the input's pixel size and
   `[left, top, right, bottom]` as fractions of the answer.
5. The three clients crop `image.picture` to `content`; the playground does the same before drawing.
   `image.data` stays the model's file, canvas and all.

A named ratio is a deliberate reframe: the picture is sent unchanged with that ratio, the answer has
no `frame`, and `warnings[0].code` is `reframed`.

## Measurements, 2026-11-06

A 512×256 crop (2:1) of `gwfss` row 3, uploaded through the public API:

| call | latency | answer |
|---|---|---|
| `auto` (1K) | 17.1 s | 1376×768, `frame` 512×256, content `[0, 0.0556, 1, 0.9444]`; the crop is 1376×682 |
| `aspect_ratio=16:9` (1K) | 16.4 s | 1376×768, no `frame`, `reframed` warning |

The generated canvas is not letterboxed: the model outpainted the band with more scene, so the crop
keeps the picture's own band and discards invented pixels above and below. The source's composition
— the vertical head at top centre, the horizontal one at bottom right, the round one at left — is
kept; the pixels are the model's.

The playground's cached example was re-warmed under the query the page now sends
(`aspect_ratio=auto`): first call 17.7 s (miss), second 0.049 s with `cached: true`. `gwfss` row 3 is
512×512, so `auto` resolves to 1:1, `content` is `[0, 0, 1, 1]`, and the picture is unchanged.

## What changed

- `models/nanobanana/images.py`: `Frame` and `Reference`, `nearest_ratio`, `_canvas_size`, `_extend`
  and the new `prepare_reference`; `serve.py` resolves `auto`, passes the canvas, and writes `frame`
  or the warning; `config.py` declares `auto` first.
- `gateway/src/biohack_gateway/models.py`: `ReferenceFrame` and `ModelPrediction.frame`, with a box
  validator. `assets/play.js` cuts the answer before drawing it.
- The three clients crop the decoded picture; JavaScript does it through the entries' `framer`, since
  the core has no codec.
- The model page, the clients guide and the rules describe the flow.

## Lessons

- **A generative edit's frame is the caller's decision, not the provider's.** Sending the picture at
  its own ratio and naming another one hands the provider a choice it makes silently.
- **The crop box is fractional on purpose.** The answer's size is the model's, and the box has to
  survive that; pixels would tie the record to one generation.
- **Pictures are not letterboxed.** The model filled the band, so `content` is a frame, not a matte.
  Cropping is right; assuming flat bands would be wrong.
- **A matched frame is not registration.** The answer lines up with the input at the frame level; the
  pixels inside are a redrawing, which the page and the model page now say.
