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

## The gesture, second step

- **One editor.** `editor` holds `"boxes"`, `"points"` or null, and each switch sets it; asking to
  edit one list locks the other, so a press cannot both start a box and add a point. The points
  switch is a `cloneNode(true)` of the boxes one, given its own id and title — one control written
  twice would drift.
- **Left button on the picture** adds a point at the click, in the points mode only, and a drag of
  four pixels adds nothing: the same travel that tells a box from a click tells a point from a pan.
  **Left button on a marker** selects it and carries it. **Second button on a marker or a box**
  removes it, on both layers.
- **The row acts on the selection.** `1` / `0` / `×` sit beside the points switch and appear only
  while a point is selected; `1` and `0` are pressed to show the selected point's label. Escape clears
  the selection, Delete removes it unless the reader is typing in a field.
- **The next point's label is a selector of its own**, between the list and the switch: `1` / `0`,
  pressed for the value a press on the picture writes. Adding three negative points is three presses
  rather than six, and the entry is written with the label the row named, so the text and the marker
  agree. It is a different question from the selected point's actions and keeps a separate state.
- **An emptied list writes the empty field, not `[]`.** `_check_geometry` refuses an empty array, so
  removing the last box or point leaves `""` — "nothing to send" — which is also what `forgetBoxes`
  already did.

Two bugs the browser check found, both fixed here:

1. **Selection rebuilt the marker layer**, which detached the element the press had landed on and
   would have broken every drag in a real browser (the test that dispatched on the detached node
   still passed). `showSelection` now toggles a class on the markers it holds; only a changed list
   rebuilds them.
2. **Deleting the last box wrote `[]`**, which the contract refuses — so the overlay kept the deleted
   box and Run would have answered `400`. The commit paths now write the empty field.

Verified through Chrome against the local gateway: mutually exclusive switches, click-adds
(`[[307,307],[614,614]]`), drag moves the second to `512,512`, `0` relabels to `[[...,0]]` with a
hollow marker, right-click drops a point and a box, Delete drops the selection, and changing the row
clears the list with the actions hidden.

## Bounds, and the eye's name

- **The page is the only place that can judge a coordinate against the picture.** `parseGeometry`
  now takes the image on screen and refuses an entry past its edge — a point outside `[0, w] × [0, h]`,
  a box with a non-positive extent or an edge past the picture. The gateway deliberately leaves bounds
  to the backend and the backend does not check them: the encoder samples a normalized coordinate
  past `1` as padding, so the answer is about nothing rather than an error. The field turns red and
  its `title` carries the sentence a border cannot — the shape reason or the bounds reason.
- The lists are validated again when the picture lands (`render`), because a value typed while an
  image was still loading is only then judgeable.
- **The eye is `geometry`**, label and key: `view.geometry`, `data-view="geometry"`, and the presets'
  view defaults say `geometry` too. It was named for boxes when it was the only layer of that kind;
  it now carries the reader's boxes, the reader's points and the model's boxes.

## What is still open

- **The container still trusts the coordinates it is handed.** A client calling the API directly — not
  through the page — can still send a point past the edge and get an answer about nothing. Refusing or
  clamping after decoding is a container decision nobody has made.
- **Two labelled groups sit on one row** once a point is selected: the next point's `1` / `0` and the
  selected point's `1` / `0` / `×`, separated by the switch. Copies differ in title and in the `×`;
  a stronger distinction would be a caption, and the row has no width for one at 420 px.
