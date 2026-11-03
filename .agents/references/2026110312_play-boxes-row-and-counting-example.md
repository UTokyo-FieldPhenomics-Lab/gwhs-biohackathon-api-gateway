# The boxes row, and an example that carries boxes

2026-11-03. Two changes to `/play`, both from one report: `tasselnetv4` had no example chip, and the
`edit`/`locked` switch sat under the boxes list instead of beside it. Commands, measurements and what
broke are below; the constraints this leaves behind are in
[`rules/gateway.md`](../../.agents/rules/gateway.md) § Framed pages.

## What the page did

`MODEL_PRESETS` carried no entry for `tasselnetv4`, so the counting model — the one model whose whole
request is a list of exemplar boxes — offered no chip, and a visitor met an empty field with a
placeholder. `sam3.1-meta` had one (`18 heads`) but its call needs no boxes, so nothing exercised the
path that writes a geometry parameter from a preset.

The switch was written in `play.html` as a form grid item of its own
(`#boxes-mode {grid-column: 2}`), one row below the generated parameters block. On the deployed host
that row opened under the list's left edge; in this checkout it landed beside it, because the
generated `#parameters` block spans both columns and the switch's auto row resolves after it, which
puts it in the same band on a wide page and under the list on a narrow one. Either way it was a
second row of the form, not part of the control it switches.

## The example

Three exemplar boxes drawn on `gwhd` row 4, the paper's setting:

```json
[[306,148,55,44,1],[487,313,37,70,1],[933,573,58,43,1]]
```

Measured through the public API before it was written into the preset:
`count 41.44`, `density.size [23,23]`, answered by `homepc-4090`. That is the number a reader sees
after one click, which is the point of a chip.

## The bug the example found

`applyExample` wrote the dataset and the row, then the parameters, then called `preview()` — and
`preview()` clears the boxes when the picture changes (`forgetBoxes`, before its first `await`). The
first run of the new chip changed the picture to `gwhd` row 4 and left the field empty: the example
took its own boxes away. A parameter that names a value independent of the picture never showed this.

The order is now picture first, parameters second: `preview()` is called between the address and the
parameter writes, so the drop happens before the writes and the render that arrives later draws them.
The call is asynchronous, so nothing waits.

`applyExample` also wrote `String(value)`, which turns a list into `1,2,3,4,1`. It now writes JSON for
anything that is not already a string — the same text a reader would type, and the same text the
request carries.

## The row

- The switch is written once in the markup and moved into the `boxes` parameter's own cell
  (`cell.classList.add("geometry"); cell.append(boxesMode);`), so the list and the switch are one
  control read left to right, and the pair takes the whole control column. The input loses its
  34 rem ceiling on that row only.
- Each state is an icon and a word. The cell is a `container-type: inline-size` container, and under
  `26rem` the words go: `@container (max-width: 26rem)`. At 1440 the words show; at 390 the two icons
  do, and the row no longer pushes the switch past the right edge, which is what the old layout did on
  a phone (`locked` was cut off at 390 px, measured on `HEAD` before the change).
- Each button keeps `aria-label` and `title`, so the icon-only state still has a name.
- `showMode` now clears the picture's `drawing` cursor when the model has no boxes, which the early
  return used to skip.

Verified in the framed page at 1440, 768 and 390 with the real gateway answers behind it (a throwaway
same-origin proxy over the public API, `/tmp/opencode/play-harness.py`, not committed): the chip fills
the field and draws the three boxes on `gwhd` row 4, `sam3.1-meta` keeps its own `boxes` and `points`
rows, and a model without `boxes` leaves no switch in the document.

## A phone's sideways scroll is older than this change

The page scrolls sideways at 390 px, and it did so on `HEAD` too (same capture, same viewport). It is
not the switch — the box row now fits — and it is not the prompt textarea's box model, which was
fixed here as a one-line addition (`textarea` was missing from the `input, select` rule, so a
`width: 100%` textarea added its padding outside the width). Left alone: it is a separate defect with
its own cause, and this record exists so the next person does not think the switch caused it.
