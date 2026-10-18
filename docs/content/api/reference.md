# Base API reference

This is the underlying HTTP interface: every route, the fields of every answer, and a form that sends
the call. All three clients are wrappers over exactly this, so everything here is available to them —
you just do not have to build the URL, walk the pages, or decode the masks yourself.

**The two predict routes show no query parameters here, and that is deliberate.** Their query is a
*model's own*, and it differs per model, so the document cannot enumerate it; `GET /models/<id>` is
where the list lives, and [Model parameters](http.md#model-parameters) is how to send one. The form
below still runs a predict with no parameters at all (the model's defaults), and
[the live demo](../examples/demo.md) is the same call with controls built from that declaration.

Your key is typed into the page below, which is served by the API host itself, and stays in your
browser on that origin. This site never sees it. The same key is remembered by the
[live demo](../examples/demo.md): set it on either page and the other uses it.

<div id="reference-frame">
  <iframe
    data-api-frame
    src="https://api.global-wheat.com/docs"
    title="Global Wheat API — interactive reference"
    loading="lazy"
    style="display: block; width: 100%; height: 90vh; border: 0"
  ></iframe>
</div>
