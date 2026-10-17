# The model page's rows, what it draws on a photograph, and the call as a command

Session of 2026-10-16, third of the day, following
[`2026101617_one-key-for-both-pages.md`](2026101617_one-key-for-both-pages.md). Everything here was
driven against the same stand-in container in `/tmp/opencode/rehearse/`; nothing in this note is a
measurement of a real model.

## What the user asked for, and what each answer was

- **"1008 px should be a parameter"** — it cannot be, and the reason is the contract rather than the
  page: `resolution` is a model property (`ModelSummary.resolution`, declared by the container) while
  the controls come only from `parameters[]`. A page that offered a resolution control would be
  inventing an argument the container does not accept. If it should be adjustable, the declaration has
  to expose it as a parameter — and then the page renders it with no code change, which is the
  invariant working rather than an exception to it.
- **"too many repeated green lines"** — the duplication was `1008 px` said twice at once, once beside
  the model and once in the run line, and the run line was itself wrapped out of the image row into a
  line of its own. The model's attributes are now one muted line beside the model
  (`runs at 1008 px · 1 backend up · 1 request at a time`), the outcome moved into the figure's
  caption (`1 object · Σ decoded mask = 12,074 px, Σ area = 12,074 px — equal`), and the line beside
  the Run button carries only what is happening now — `running…`, a refusal — and is `display: none`
  while it is empty.
- **"the slider shows no number"** — the declaration carries `minimum`, `maximum` and `default` but no
  step, so the page picks the step and now prints the value with the same number of decimals
  (`0.5` → `0.50`). Measured: dragging to `0.77` moves both the readout and the command.
- **"the image cannot be seen at once"** — the pane is a fixed height and the photograph is fitted
  inside it whole, so nothing inside a picture scrolls. The height is in `rem`, never `vh`: this page
  reports its own height to the frame that holds it, so a viewport unit would be a fraction of the
  page's own content and the box would be sizing itself.
- **"swap the model and image rows"** — taken as *the model together with the knobs it declares, then
  the image with Run at the end of that row*, and shown to the user as three drawn alternatives before
  any code changed. The mess was specific: the parameters sat below the button that runs the model, on
  the far side of the image row, and the outcome line wrapped into the gap between them.
  *(Revised 2026-10-17: the user's reading was the one about causality, and it wins — a published row
  comes from the dataset surface and needs no model, so the image is now the **first** row, fetched and
  previewed before a declaration is asked for, and Run closes the parameters row instead. The order is
  image → model → parameters + Run; see
  [`2026101712_the-image-comes-first.md`](2026101712_the-image-comes-first.md).)*
- **"show the equivalent curl"** — the page prints the call the controls describe, built from those
  same controls, the page's own origin, and nothing written down twice.

## Numbers from the browser

A one-row index of `gwhs-temp` row 0 — 592×2509, and one object with `bbox [231, 923, 126, 162]`,
`area 12074`, `score 0.582` — in a page whose pane measured 918×512:

| what | measured |
|---|---|
| stage (the image's own box) | 121 × 512 — the whole row, fitted by its height |
| the photograph's share of the pane's width | 13 % — this is what "see it all at once" costs on a 1:4.2 row |
| the box, in the bitmap's pixels | `[231, 922, 134, 170]` against the declared `[231, 923, 126, 162]`; the extra 8 px each way is the 1.5 px border at that display scale |
| the mask's own pixels | `[231, 923, 356, 1084]`, inside the box, 12,074 of them — equal to `area` |
| the score label | `0.58`, its bottom edge 0.8 px below the box's top edge, 3 px wider than the box it labels |
| the pane, scrolling | `scrollHeight === clientHeight`: nothing scrolls |

The label's overflowing its box by 3 px is the thing to watch with many objects: at this display size
a box is ~26 px wide and a four-character score is ~29 px, so neighbouring boxes on a tall row can
have their labels touching. Nothing was done about it — the panes' height, a smaller label, or a label
only on the box that is hovered are all ways out, and it is the user's call.

## Two bugs, one of them in the tooling

- **The caption lost its colour.** `say()` puts the `good`/`bad` class on `#check`, and `#check` moved
  from *being* the `<figcaption>` to being a `<span>` inside it — so `#play figcaption.good` no longer
  matched and the outcome line rendered muted. Caught by reading `getComputedStyle`, not by looking at
  a screenshot: the vision pass read that same line as "blue/teal", which would have been a wrong
  conclusion drawn from a right observation.
- **A running server can predate the file it serves.** The page was verified in Chrome against a
  gateway started at 22:23:18, while `play.html` was last written at 22:25:58 — so the browser was
  driving the page from *before* the change, and the first browser check failed on a missing element
  that the file on disk plainly had. The asset files are read at import, so a restart is what makes a
  change live, and **the served bytes have to be checked after the restart** (`curl … | grep -c`) rather
  than assumed. This cost two round trips; it will cost them again without the grep.

A third thing looked like a bug and was not: `new Event("input")` does not bubble, so a dispatched
synthetic event never reaches a delegated listener — the slider appeared not to refresh the command,
while a real drag does. Any probe of delegated events has to dispatch with `{bubbles: true}`.

## The command is the same call, and that was checked by running it

The command was taken off the page verbatim, run in `bash` with a real key exported, and compared with
the page's own run in the stand-in container's log:

```
predict /models/sam3.1-meta/predict?prompt=wheat+head&confidence_threshold=0.5  content-type='application/json' bytes=73
predict /models/sam3.1-meta/predict?prompt=wheat+head&confidence_threshold=0.5  content-type='application/json' bytes=73
predict /models/sam3.1-meta/predict/upload?prompt=wheat+head&confidence_threshold=0.5  content-type='image/jpeg' bytes=9499
```

The first two are the page's run and the command, byte for byte; both answers parsed equal to the
recorded response. The upload command was run from a directory holding a file named
`wheat field's photo.jpg` — a space and an apostrophe — because the quoting is the part that would
break silently: the page emits `-F 'file=@wheat field'\''s photo.jpg'`, which is the standard idiom
and which curl accepted.

The key is left as `$KEY` and never printed. The values are single-quoted for the same reason the
gateway never concatenates client input into a path: a parameter's value is whatever the model
declared and whatever the reader typed, and a command line is not the place to discover that one of
them contained a quote. In practice `URLSearchParams` already percent-encodes the query — `wheat head
a'b $(whoami) "q"` came out as `wheat+head+a%27b+%24%28whoami%29+%22q%22` — but the body and the file
name are not encoded by anything, which is where the quoting does the work.

## What is still open

The same three as before — the recorded response for the demo panel, the rehearsal against a real GPU
backend, and the second-model invariant check — plus one this session created: on a 592×2509 row the
picture is a 121 px-wide strip in a 918 px pane, 13 % of its width, and whether that is acceptable, or
wants a taller pane or a way to open the mask at full size, is a question the user has not answered
yet.
