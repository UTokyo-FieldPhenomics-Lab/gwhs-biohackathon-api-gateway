# Live demo

The panel below is served by `api.global-wheat.com` and framed here, so it is **same-origin with the
API**: no cross-origin call is involved anywhere, and your key is typed into the API's own page — this
site never sees it. The [reference](../api/reference.md) remembers the same key on the same origin:
set it on either page and the other uses it, Forget on either clears it for both.

Pick a row or drop in a photograph, pick a model, press **Run** — the answer is drawn on the picture,
and the corner says which machine produced it. **Examples** fills in a call that was measured to work;
one chip and Run is the shortest way to a first answer, and a chip only appears while its dataset and
its parameters are still what the service serves. The `results` eye shows whatever pixels the model
made: a backbone's patch grid over the photograph, or a generated frame in place of it. A model that
answers in words puts them under the picture.

Under the panel the page prints the same call as a `curl` command, with your key left as `$KEY`. To
check that your **own** client works rather than the service's, run the
[notebook](quickstart.md) — it records its outputs, so a difference is the thing to debug.

<div id="play-frame">
  <iframe
    data-api-frame
    src="https://api.global-wheat.com/play"
    title="Global Wheat API — run one model"
    loading="lazy"
    style="display: block; width: 100%; height: 90vh; border: 0"
  ></iframe>
</div>
