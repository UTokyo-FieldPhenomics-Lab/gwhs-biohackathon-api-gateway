# Live demo

This page runs the model without you installing anything. The panel below is served by
`api.global-wheat.com` and framed here, so it is **same-origin with the API**: no cross-origin call is
involved anywhere, and your key is typed into the API's own page — this site never sees it. The
[reference](../api/reference.md) remembers the same key on the same origin: set it on either page and
the other uses it, Forget on either clears it for both.

## Run one

Paste your team's key, pick a row or a photograph, and press Run. The image is drawn as soon as the
address is complete — whole, at whatever size the page can show it in — with each box labelled by the
score the model gave it, and the mask decoded in your browser by the same decoder this site hands you.
The model line says how many machines are answering and how long ago the gateway last asked them, and
keeps that age climbing rather than letting a stale count pass for a live one.

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

## A recorded call, decoded here

No key, no request: the panel below is one real answer, made earlier on `gwfss` row 4 with
`confidence_threshold` 0.25, and the call is replayed in your browser. The masks are decoded by the
same file you would download, and the caption is the check that makes it worth trusting — the decoded
pixel count against the `area` the API reported for each object.

<p id="demo-state" role="status">Loading the recorded call…</p>

<figure id="demo-figure" hidden>
  <figcaption id="demo-check"></figcaption>
  <canvas id="demo-canvas"></canvas>
</figure>

<style>
  /* Scoped to this page's panel. The image is shown at the column's width, in a box of its own: a
      large row would otherwise be several screenfuls of page with the object somewhere in the middle
      of it. The script scrolls the box to the object once it has drawn one. */
  #demo-figure {
    max-height: 28rem;
    overflow-y: auto;
    margin: 1rem 0;
    border: 1px solid var(--md-default-fg-color--lightest);
    border-radius: 0.2rem;
  }
  #demo-canvas {
    display: block;
    width: 100%;
    height: auto;
  }
  #demo-figure figcaption {
    position: sticky;
    top: 0;
    padding: 0.4rem 0.6rem;
    background: var(--md-default-bg-color);
    border-bottom: 1px solid var(--md-default-fg-color--lightest);
    color: var(--md-default-fg-color--light);
    font-size: 0.8rem;
  }
  #demo-state { color: var(--md-default-fg-color--light); font-size: 0.8rem; }
  #demo-check[data-equal="no"] { color: var(--md-typeset-del-color); }
</style>

<script src="../clients/global-wheat.js"></script>
<script src="demo.js"></script>

A recorded answer is **one machine's** answer, and `computed_by` is where the record says so: the same
call on another card can differ by a pixel, by a percent of a mask's area, or by one object at the
margin. [The model's page](../models/sam3.1-meta.md) is where the numbers behind that table live.
