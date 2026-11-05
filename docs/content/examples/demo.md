# Live demo

The panel is served by `api.global-wheat.com` and framed here, so it is **same-origin with the API**:
your key stays on the API's own page, and the [reference](../api/reference.md) remembers it on that
origin. Pick a dataset image or drop in a photograph, choose a model and press **Run** — the answer is
drawn on the picture, and the corner names the machine. **Examples** fills a measured call that still
matches what the service serves; the `results` eye shows a backbone grid or a generated frame, and
prose appears under the picture.

A model that declares `boxes` or `points` takes them from the picture: draw on it, and the list beside
the model shows what you drew. One mode group chooses add-positive, add-negative or remove, and the
two lists are edited one at a time, so a click is never ambiguous. The `prompt` field is the one control
kept across model changes; every other control starts from the model's declaration. Empty it to forget
the phrase.

The panel prints the same call as `curl`, with your key as `$KEY`. To check your own client, run the
[notebook](quickstart.md) — its recorded outputs make a difference visible.

<div id="play-frame">
  <iframe
    data-api-frame
    src="https://api.global-wheat.com/play"
    title="Global Wheat API — run one model"
    loading="lazy"
    style="display: block; width: 100%; height: 90vh; border: 0"
  ></iframe>
</div>
