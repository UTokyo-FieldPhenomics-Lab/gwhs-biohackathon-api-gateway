# One colour for every class: the live demo could not show a semantic answer

Reported 2026-10-19: on `https://api.global-wheat.com/play`, `gwfss-segformer-b4`'s answer painted
all three classes in the same pale red, so `head`, `stem` and `leaf` were indistinguishable. Fixed in
`c604b2d`; the site's demo page carries the matching sentence.

## What it was

`assets/play.js` had one constant for every mask:

```js
const MASK = [255, 105, 180, 110];
```

That is a fine default for an instance answer, where the objects are what a reader is counting, and
it was written when `sam3.1-meta` was the only model. A semantic answer is a different shape: three
objects, one per class, and the class is the only thing worth reading. Three overlapping pink tints
are one pink tint.

A second defect came with the first: `head` and `leaf` both answer with `bbox` `[0, 0, 512, 512]`
(class extents, not instances), so their labels landed on the same point and the later one covered
the earlier one. The screenshot that started this had two labels for three objects.

## What it is now

- A category the picture's dataset declares is tinted in the dataset's own colour, read from
  `GET /datasets/<id>`'s `classes[].color` and cached per dataset for the page's lifetime. The page
  holds no class name and no colour literal for a class, which is what
  `test_the_model_page_takes_a_class_colour_from_the_dataset` now enforces.
- A category that dataset does not declare — a detector's text prompt, repeated on every neighbour —
  is not a class of the picture. Those objects cycle a six-colour fallback by object, so neighbours
  differ, and their boxes keep the score alone rather than a label as long as the frame.
- The legend above the picture names each declared class, in its colour; an instance answer shows no
  legend, because one chip would claim a colour several objects do not share.
- Box borders and label backgrounds take the object's colour; label ink is black or white by the
  colour's luminance.
- Labels whose anchors coincide stack by 18 px instead of hiding each other.

## Verified live

Row 3, `gwfss-segformer-b4`, `lab-5090`:

| class | swatch computed | label |
|---|---|---|
| `head` | `rgb(50, 255, 132)` | `head 0.92` |
| `stem` | `rgb(50, 132, 255)` | `stem 0.64` |
| `leaf` | `rgb(214, 255, 50)` | `leaf 0.76`, `top: 16.5px` |

`3 objects · Σ decoded mask = 133,838 px, Σ area = 133,838 px — equal`, unchanged. The masks in the
screenshot are green, blue and yellow-green respectively.

`sam3.1-meta`, row 4, `prompt="wheat head"`, threshold 0.25: 17 objects, legend hidden, labels
score-only, six distinct box colours cycling by object. The object count differs from the recorded
18 by the margin the demo page already warns about for another card.

## Deployment notes

- Assets are read at import, so a change needs `sudo systemctl restart biohack-gateway` on the host;
  a pull alone changes nothing.
- The first pull missed the commit because it had not been pushed yet; `git log -1` on the host is
  the cheap check before blaming the cache. The page's own `grep paletteFor` is the check after.
- The palette read is a metadata call and starts alongside the prediction, not before it; a failed
  read caches an empty map and falls back to per-object colours rather than failing the run.
- `startOver()` clears the cache: it was filled with the key that just went.
