# JavaScript client

One core and two entries. `global-wheat.js` holds the routes, parameters and decoders; the entry you
load decides how an upload is made smaller — canvas in a browser, `sharp` in Node. A page that only
reads images can load the core alone.

## Setup

=== "Node"

    Node 18 or newer. In an empty directory:

    ```bash
    npm init -y && npm pkg set type=module
    curl -O https://docs.global-wheat.com/clients/global-wheat.node.js \
         -O https://docs.global-wheat.com/clients/global-wheat.js
    npm i sharp
    export GLOBAL_WHEAT_API_KEY='YOUR_TEAM_KEY'
    ```

    The two files must sit together: the entry imports the core beside it. The `package.json` makes
    Node read them as ES modules; without it the `import` fails.

=== "Browser"

    ```html
    <script type="module" src="https://docs.global-wheat.com/clients/global-wheat.web.js"></script>
    ```

    ```js
    import {Client} from "https://docs.global-wheat.com/clients/global-wheat.web.js";
    ```

    The web entry imports `global-wheat.js` beside it, so serve both. The API sends no CORS header:
    a page on another origin cannot call it. The one browser origin that works is the API's own, which
    is how the [live demo](../../examples/demo.md) is built; anything else goes through your own
    server, because a token in a browser page is a token everyone can read.

## Run an example

Save this as `example.mjs` beside the two files, then run `node example.mjs`:

```js
import {Client} from "./global-wheat.node.js";

const client = new Client(); // key and host from the environment

const row = await client.image("gwhd", 0);
console.log(row.width, "x", row.height, "—", row.objects.length, "objects");

const answer = await client.predict("sam3.1-meta", "gwhs-temp", 0, {prompt: "wheat head"});
console.log(answer.model, "at", answer.resolution_used, "px");
for (const found of answer.objects) {
  console.log(found.idx, found.score.toFixed(3), Math.round(found.area));
}
```

`gwhs-temp` is the development sample of `gwhs`, so this call works before the full annotation is
released. On your own photograph, use `predict_file("sam3.1-meta", file, {prompt: "wheat head"})`,
where `file` is a `File` or `Blob`: a browser gets one from an `<input type="file">`, and a Node
script from `const file = await readFile("photo.jpg")`, imported from `node:fs/promises`.

## Pass model parameters

Parameters are properties of the options object, with the names `GET /models/<id>` publishes. An
unknown name is a `400`, not a silent default.

```js
const sam = await client.predict("sam3.1-meta", "gwhs-temp", 0, {
  prompt: "wheat head",
  confidence_threshold: 0.5,
});

const counted = await client.predict("tasselnetv4", "gwfss", 0, {
  boxes: [[312, 143, 44, 55, 1]],
});
```

A geometry parameter is one value — `boxes` or `points` as an array — never repeated names.
`decode` and `resizeBeforeSubmit` are the client's own options and are never forwarded. The [model
pages](../../models/index.md) say what each parameter does; [Model
parameters](../http/index.md#model-parameters) is the wire contract.

## Read decoded outputs

- A mask is `{data: Uint8Array, width, height}`, row-major: `data[y * width + x]` is the pixel at
  `(x, y)`. The raw `rle` stays beside it.
- A counting answer's `density.grid` is an array of rows; a backbone's `features.grid` is one flat
  `Float32Array` of `rows * columns * dim` values.
- A picture is `image.picture`, a `Uint8Array`. On an edit that carries `frame`, it is already cropped
  to that box — the shape of the picture you sent — while `image.data` remains the model's full file.

## When it goes wrong

A refusal raises `ApiError` with `status`, `code` and the API's own `message`. Nothing is retried for
you: `429` means wait, `503`/`504` are worth one more call, and `400` needs the request fixed.
[Refusals](../http/index.md#refusals) lists every code.

## Next

- [Overview](index.md) — the shared methods and calling conventions
- [Python](python.md) · [Java](java.md) — the same calls in the other clients
- [Jupyter quickstart](../../examples/quickstart.md) — the same API, one step at a time
