# The play page's view: four eyes, and one default row per model

Follow-up to [`2026101918_play-one-colour-for-every-class.md`](2026101918_play-one-colour-for-every-class.md),
2026-10-19. Commits `dbe9f20` (the page) and `9b83c5a` (formatting).

## What the page shows now

The figure's caption row is one line: the outcome on the left, then four eyes with the word each
governs, then `raw image ↗`.

| eye | default | what it hides |
|---|---|---|
| masks | on | the decoded mask layer over the photograph |
| boxes | on, except where a model's row says otherwise | the box, its class and its score |
| legend | off, except where a model's row says otherwise | the class chips in the viewer's top-left corner |
| watermark | on | the `computed_by` chip in the corner |

State lives in `VIEW_BASE` and `VIEW_BY_MODEL` at the top of `play.js`, merged when a declaration
arrives. Only one row differs from the base:

```js
const VIEW_BY_MODEL = {
  "gwfss-segformer-b4": {boxes: false, legend: true},
};
```

A semantic model's boxes are class extents and cover most of the frame, so they open hidden and the
legend — which then carries the class name and, for a class with one object, its score — opens shown.
An unknown id takes the base view, which is also what a renamed model gets.

Both corner labels live on the pane rather than on the image: a portrait row leaves a band at each
side, and the legend belongs in the band rather than over the photograph. It starts 8 px from the
pane's top-left, so on a square row it clears the image entirely; the machine chip does the same at
the bottom-right, four lines tall.

This is the one place the page writes a model id. `test_the_model_page_names_no_model_and_no_parameter`
was narrowed rather than dropped: `sam3.1` still appears nowhere, and the id above appears exactly
once. `rules/gateway.md` records the exception: the table decides what is *shown* first, never what
is sent.

## Why the legend can carry the score

`head 0.92` used to exist only as a box label, and two classes with the same extent put their labels
on the same pixel. With the boxes hidden by default, the score would have left the screen entirely.
A chip now reads `head 0.92` when that class has exactly one object; a class with several instances
keeps the name alone, because one number would misrepresent the rest.

## A legend that vanished on another dataset

Reported the same evening: run once on `gwfss` and the legend appears; change the image and run
again, and it is gone — and the legend eye cannot bring it back.

The chips were built only for categories whose colour came from the dataset's palette, so a run of
`gwfss-segformer-b4` on `gwhd` (whose palette is spikelet classes) or on an uploaded photograph
(which has no palette at all) drew its three classes in the fallback colours and then named none of
them. The eye was pressed, `showLegend` produced no children, and `render` hid the empty panel: a
toggle over nothing.

A category is now listed when **either** the dataset gave it one colour (every object of it shares
that colour) **or** it has exactly one object, whose own colour then stands for the category. The
box label follows the same predicate, so `head 0.65` appears on the box as well. An instance answer
with seventeen heads still lists nothing — no single chip can stand for six colours — and its eye
therefore still shows an empty legend.

Verified live: `gwfss` row 3 gives the palette chips (`rgb(50,255,132)`, `rgb(50,132,255)`,
`rgb(214,255,50)`); `gwhd` row 0 gives `head 0.65`, `stem 0.67`, `leaf 0.67` in the fallback colours
(`rgb(255,79,163)`, `rgb(0,194,255)`, `rgb(255,176,0)`), matching the masks drawn beside them; the
eye hides and restores those three chips; `sam3.1-meta` still lists none.

## Decode once, draw many

`paint()` decodes the answer into **one** mask layer at display size (≤ 2600²), keeping the object
counts and the address; `render()` draws photograph + layer + boxes + legend + watermark from that.
A toggle is `render()` and nothing else — no request, no second decode, no re-fetch of the palette.
The old per-object offscreen canvases are still created one at a time inside `paint` and dropped, so
peak memory is one mask, not the answer.

## Caption, shortened

| before | after |
|---|---|
| `3 objects · Σ decoded mask = 133,838 px, Σ area = 133,838 px — equal` | `3 objects · Σ mask area = 133,838 px` |
| `3 objects · none carrying an area and an RLE to check` | `3 objects · no area or RLE to check` |
| `gwfss row 3 — 512×512 px, press Run to segment it` | `gwfss row 3 · 512×512` |
| `the decoder did not load … — the response body below is complete` | `3 objects · decoder did not load — the response body is complete` |

The unequal case keeps both numbers: `3 objects · Σ mask area 133,838 ≠ Σ area 133,839 px`, in red.

## Verified live

`gwfss` row 3, `gwfss-segformer-b4`, `lab-5090`:

- default view: `masks=true boxes=false legend=true watermark=true`;
  caption `3 objects · Σ mask area = 133,838 px`; chips `head 0.92`, `stem 0.64`, `leaf 0.76`;
  0 `.mark` elements; watermark present.
- clicking `boxes`: 3 marks with the same three labels; clicking `legend`/`watermark`/`masks` each
  hides only its own layer, and no page error is raised.
- `sam3.1-meta` row 4, `wheat head` 0.25: switching applies the base view (`boxes=true legend=false`),
  17 objects, 17 marks, legend hidden, caption `17 objects · Σ mask area = 102,002 px`.

## Deploying it

Assets are read at import: `git pull` on the host, then `sudo systemctl restart biohack-gateway`.
A curl immediately after the restart can hit the brief window before the new process binds and
return an empty page — check `grep -c VIEW_BY_MODEL` on the served page rather than reading a
grep failure as a deploy failure.
