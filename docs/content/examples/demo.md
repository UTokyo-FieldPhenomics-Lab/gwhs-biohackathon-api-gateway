# Live demo

This page runs the model without you installing anything. The panel below is served by
`api.global-wheat.com` and framed here, so it is **same-origin with the API**: no cross-origin call is
involved anywhere, and your key is typed into the API's own page — this site never sees it. The
[reference](../api/reference.md) remembers the same key on the same origin: set it on either page and
the other uses it, Forget on either clears it for both.

## Run one

The panel opens on a model a first-time reader can run with no parameters, and a model's own
**Examples** buttons fill in an address and the parameters a call was measured with — one chip and
Run is the shortest way to a first answer. Nothing is sent until you press it.

The image is drawn as soon as the address is complete — whole, at whatever size the page can show it
in — with each box labelled by the score the model gave it, and the mask decoded in your browser by
the same decoder this site hands you. A class the dataset declares is tinted in the colour that
dataset publishes — the same palette its mask images use — and the legend in the picture's corner
names them. An instance model answers with objects rather than classes, so each of those is given a
colour of its own instead. The model line says how many machines are answering and how long ago the
gateway last asked them, and keeps that age climbing rather than letting a stale count pass for a
live one.

The `results` eye is the layer for whatever a model produces as pixels, in whichever of its two
shapes it takes. A backbone answers with a feature grid, and the first three principal components of
that grid are drawn over the photograph; the `image` eye turns the photograph off so the field is
read on its own. A model that *makes* a picture answers with a PNG, and that picture replaces the
photograph instead of tinting it — the `image` eye is what brings the input back. The colours belong
to that one picture; the [backbone model page](../models/dinov3.md) says what a grid is and what can
be computed from it.

A model that answers in words puts them under the picture, in a bar you can close.

The page also prints the call it would make as a `curl` command, with your key left as `$KEY`, so
anything you set up here can be taken straight to a shell.

<div id="play-frame">
  <iframe
    data-api-frame
    src="https://api.global-wheat.com/play"
    title="Global Wheat API — run one model"
    loading="lazy"
    style="display: block; width: 100%; height: 90vh; border: 0"
  ></iframe>
</div>

## Start from an example

A model's **Examples** buttons fill in an address and the parameters a call was measured with, so the
shortest way to a first answer is one chip and Run — nothing is sent until you press it. A chip is
only offered while its dataset is still in the directory and the model still declares the parameters
it names, so a button that is there is a call the service can answer now.

To check that your **own** client works rather than the service's, run the
[notebook](quickstart.md): it records its outputs, so a difference is the thing to debug rather than
a reason to doubt the setup.
