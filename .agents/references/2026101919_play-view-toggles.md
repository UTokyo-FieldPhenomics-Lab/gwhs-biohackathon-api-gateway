# The play page's view: four eyes, and one default row per model

Follow-up to [`2026101918_play-one-colour-for-every-class.md`](2026101918_play-one-colour-for-every-class.md),
2026-10-19. Commits `dbe9f20` (the page) and `9b83c5a` (formatting).

## What the page shows now

The figure's caption row is one line: the outcome on the left, then four eyes with the word each
governs, then `full size ↗`.

| eye | default | what it hides |
|---|---|---|
| masks | on | the decoded mask layer over the photograph |
| boxes | on, except where a model's row says otherwise | the box, its class and its score |
| legend | off, except where a model's row says otherwise | the class chips under the caption |
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

This is the one place the page writes a model id. `test_the_model_page_names_no_model_and_no_parameter`
was narrowed rather than dropped: `sam3.1` still appears nowhere, and the id above appears exactly
once. `rules/gateway.md` records the exception: the table decides what is *shown* first, never what
is sent.

## Why the legend can carry the score

`head 0.92` used to exist only as a box label, and two classes with the same extent put their labels
on the same pixel. With the boxes hidden by default, the score would have left the screen entirely.
A chip now reads `head 0.92` when that class has exactly one object; a class with several instances
keeps the name alone, because one number would misrepresent the rest.

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
