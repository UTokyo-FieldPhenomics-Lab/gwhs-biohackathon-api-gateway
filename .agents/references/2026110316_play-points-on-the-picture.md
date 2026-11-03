# Points on the picture: the typed list is the first half

The `points` parameter was a text field with no second view, while `boxes` had an overlay, a switch,
and a drag. This note records the decision to build the drawing side in two steps and the first step
that landed on 2026-11-03: a value typed into the field is drawn where it says. The gesture — add,
move, flip, delete — is the second step and is not in this change.

## What the field may hold

`points` is `[[x, y]]` or `[[x, y, label]]` in the image's pixels, and `boxes` the same with four
coordinates. The label is optional and means positive when absent; when present it is `1` or `0`.
That is not a description of SAM 3.1: `gateway/src/biohack_gateway/models.py::_check_geometry`
refuses any other shape for **every** model declaring a geometry parameter, and
`models/sam31_official/serve.py::parse_geometry` refuses it again behind the gateway. A third number
of `3` is a `400` before any machine is asked.

**What neither check does is bound the coordinates.** The gateway's docstring says a coordinate's
being inside the image is the backend's business; the backend normalizes by the image's size and the
vendored encoder maps `[0, 1]` to `[-1, 1]` before `grid_sample`, which samples zeros outside the
feature map. So an out-of-image point runs, and answers nonsense, rather than failing. Only the page
knows the image's pixel size at typing time, so a bounds warning belongs there if anywhere; it is not
in this change.

## Decisions

- **The marker shows the number, not `+`/`−`.** The wire value is the contract; `1`/`0` is its whole
  vocabulary today. A marker that printed `+`/`−` would be a second vocabulary to keep in step, and a
  wider contract would need it rewritten. The circle carries the entry's own number, and 1 filled /
  0 hollow is only how the two known values are drawn.
- **The page edits only `1` and `0`.** A general label taxonomy (`1`–`5`) is a different product: it
  would need the declaration to publish a label domain, and this page is a participant's window on
  the API rather than an annotation tool. The display stays general; the editing does not.
- **One picture editor, later.** Boxes are editable and points are not yet, so the two cannot fight
  for a press. When the gesture lands, turning one field's edit on will lock the other: one press
  cannot both start a box and add a point.
- **Right-click is reserved for deleting a point.** The other mainstream choices were surveyed and
  rejected or deferred: CVAT deletes a vertex from a right-click menu; Meta's SAM 2 demo uses
  left/right click for positive/negative and a two-button Add/Remove toggle. Deleting on right-click
  is the one convention that needs no extra mode, and mobile reaches the same action through a
  selected marker's `×` — so no gesture depends on a right button.
- **No repeated-click label cycling.** It is not a convention in any tool surveyed, it makes a
  double click flip twice, and it takes the click that "select" needs.

## What landed

- `gateway/src/biohack_gateway/assets/play.js`: `parseGeometry(text, type)` mirrors the gateway's
  shape check for both types; `parseBoxes` is now its boxes case, which also tightens what the box
  overlay accepts (an illegal label used to draw and only fail at Run).
- A `points` input is wired to `syncPoints`, which parses on every keystroke, marks the field with
  `aria-invalid` on a shape error and keeps the last list that parsed. `forgetPoints` clears it, and
  `forgetGeometry` clears both lists when the picture changes — the same rule the boxes already had.
- `play.html` gains `#points`, above `#boxes`, `aria-hidden` because the input is the list and the
  overlay is its picture. `play.css` draws an 18 px circle in the reader's orange with the label as
  text; a negative point is hollow so the difference is a shape and a number, not only a hue. A box
  whose label is `0` carries a `0` badge: positive is what an absent label means, so only the value
  that changes the claim is written on it.
- Both geometry layers answer to the existing `boxes` eye, because they are the same kind of thing on
  the picture and a second eye would be a control with no new question.

Checked in a local gateway over the published index on 2026-11-03: `[[200,250,1],[300,320,0],[400,120]]`
drew `1`, `0`, `1` at those places; appending `x` turned the border red and left the three markers
standing; `[[459,297,86,154,0]]` in `boxes` drew a box with a `0` badge.

## What is still open

- **The gesture**, in the agreed shape: modes exclusive (points vs boxes), left click adds with the
  row's chosen label, left click on a mark selects it, drag moves it, right click on a mark deletes
  it, and the selected mark's row carries `1` / `0` / `×` so touch needs no right button.
- **A bounds check**, if it is wanted: the page has the image, the gateway deliberately does not, and
  the container could clamp or refuse after decoding. Each is a different promise; none is made yet.
- **The `boxes` eye's name** now covers points too. Renaming it to "geometry" is a label change with
  its own test, not a fact this change had to settle.
