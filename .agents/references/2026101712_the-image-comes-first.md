# The image comes first: the page's rows in the order the data arrives

Session of 2026-10-17, a correction to
[`2026101622_play-page-layout-annotations-and-curl.md`](2026101622_play-page-layout-annotations-and-curl.md).
The user rejected the row order that session settled on, and gave a reason that is about the data
rather than about taste.

## The argument, and why it wins

The order had been **key → model → parameters → image + Run**. The user's objection:

> the image should be above the model: a dataset's static API gives you images on its own, and model
> inference is something that can only happen after an image exists.

That is a statement about causality, and it is checkable in the contract: a published row is resolved
by the dataset surface (`GET /datasets/{id}/images/{row}`), which needs no model, no declaration and no
GPU — the image exists whether or not any model is answering. So the page was asking the reader to
choose *how to look* before choosing *what to look at*, and it was reading `/models` before `/datasets`
in `connect()` for no better reason than the order the surface happens to be documented in.

The counter-argument the earlier session made — that Run should sit above the model it runs — is only
about the button, and the button has a separate answer below. The rows now follow the data.

## What changed

- Rows are **image → model → parameters + Run**. Nothing else about a row changed: the key row, the
  model's facts line with its age, the parameters row with its readouts, the viewer.
- **Run moved to the end of the parameters row**, because the image row is no longer the last one. The
  button's home was always "the last control row", not "the image row" — it is the last thing read, and
  pressing it runs everything to its left. For a model that declares no parameters the row is then just
  the button at the right, and the `Parameters` legend is hidden rather than labelling nothing.
- **`connect()` fetches `/datasets` first and previews before it reads the model list**, so a reader
  whose model backend is down still sees their image. The preview never needed a model; only the order
  of two calls hid that.
- The generated controls are now a group (`<span id="parameters">`) inside the last row rather than a
  row of their own. That is not cosmetic: `showParameters()` empties that container with
  `replaceChildren()`, so a Run button placed inside it would be destroyed by the next model change.

## Verified

In the browser, on the same stand-in rig: the rows come out as `connect → Image → Model →
parameters-row`, the Run button measures flush with the right edge of the last row (0 px from it), the
`Parameters` legend and its controls are on one line, and a run still answers `1 object · Σ decoded
mask = 12,074 px, Σ area = 12,074 px — equal` with the box labelled `0.58`. The page test that pinned
the old order now pins the causal one: `source < model < parameters < run`.

## The lesson worth keeping

Two sessions argued about this row order from *reading* order — what a form should present first. The
answer was in the *data* order: which call can be made without the other. When a form's order is
disputed, the contract usually decides it.

## The bug that came back from the field

Reported after the deploy: choosing "your own photograph" and picking a file left the last published row
on screen, under a caption naming that row, until Run replaced it. The cause was one line — `preview()`
returned early unless the source was a row, so nothing in upload mode ever touched the viewer, and the
picture no longer matched the controls that describe it. A file is local, so it is now previewed the way
a row is (`URL.createObjectURL`, painted with no masks, the caption's size taken from the image itself),
and a source that names no picture clears the viewer instead of leaving the previous one. The object URL
is released when another picture replaces it.

The first version of that fix **killed the whole page**, and the tests did not notice. `addEventListener`
takes the handler by value, so `$("file").addEventListener("change", preview)` evaluates `preview` at
registration — and it sat above `const preview = …`, which is a temporal-dead-zone `ReferenceError`. The
script stopped there: no connect, no key, an empty page. A test that greps for `preview` in the served
text cannot see this; driving the page for ten seconds does. The listener now lives beside the other
preview triggers, below the declaration, with a comment saying why the position matters.

Two things worth carrying: a page's behaviour is not testable by string, so every change to one gets a
browser pass before it is believed; and a *listener registration* is an eager reference, unlike the
closure inside it.
