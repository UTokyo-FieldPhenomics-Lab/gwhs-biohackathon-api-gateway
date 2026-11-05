# JavaScript client

Two entries over one core: `global-wheat.js` is the routes, parameters and decoders, and the entry
you load decides how an upload is made smaller. The browser entry uses canvas; the Node entry uses
`sharp`. The core itself has no image code, so a page that only reads rows can load just the core.

## Load it

=== "Browser"

    ```html
    <script type="module" src="https://docs.global-wheat.com/clients/global-wheat.web.js"></script>
    ```

    ```js
    import {Client} from "https://docs.global-wheat.com/clients/global-wheat.web.js";
    ```

    Both forms are modules, because the file is one. The `<script>` form also leaves the same names
    (`Client`, `ApiError`, `rle_decode`, `label_masks`) on `globalThis.GlobalWheat`, with the
    browser client. A plain `<script>` will not do: a browser refuses `export` outside a module, and
    the file would fail to parse.

    `.web.js` imports `global-wheat.js` beside it, so download both. **Where it runs matters.** The
    API sends no `access-control-allow-origin`: a page on another origin cannot call it, and it
    should not, because a token in a browser page is a token everyone can read. The one browser
    origin that works is the API's own, which is how the [live demo](../../examples/demo.md) is
    built; anything else goes through your own server.

=== "Node"

    ```bash
    curl -O https://docs.global-wheat.com/clients/global-wheat.node.js \
         -O https://docs.global-wheat.com/clients/global-wheat.js
    npm i sharp
    ```

    ```js
    import {Client} from "./global-wheat.node.js";
    ```

    Both files have to sit together, because the entry imports the core beside it. They are ES
    modules: add a `package.json` with `"type": "module"` beside them — `npm init -y && npm pkg set
    type=module` — or Node reads the `.js` files as CommonJS and the `import` fails.

    `sharp` applies the photograph's EXIF orientation and shrinks it to the side the model runs at
    before the upload; the entry imports it even when a call does not upload. Node 18 or newer
    supplies `FormData` and `Blob`, so nothing else is installed.

## A runnable example

Save this as `example.mjs` beside the two files, in the directory whose `package.json` has
`"type": "module"`. With `GLOBAL_WHEAT_API_KEY` set, it prints one row and one model answer:

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

## Calls and parameters

The full method list is on the [overview](index.md#methods); these are the calls and options you
will use most:

| call | what you pass | notes |
|---|---|---|
| `new Client({key, base, timeout, fetch, resizer})` | a token, or the environment | construction fails without a key |
| `image(dataset, row, {decode = true})` | an address | `decode: false` leaves `rle` as the wire wrote it |
| `images(dataset, {limit = 100, cursor, ...filters})` | filters beside `limit`/`cursor` | the page reports `next_cursor` |
| `iter_images(dataset, options)` | the same filters | an async generator; use `for await` |
| `predict(model, dataset, row, {decode = true, ...params})` | the model's own parameters | `decode: false` keeps raw fields |
| `predict_file(model, image, {decode = true, resizeBeforeSubmit = true, ...params})` | `Blob`, `File`, `Uint8Array` or `ArrayBuffer` | `resizeBeforeSubmit: false` sends the original bytes |
| `generate(model, {decode = true, ...params})` | `prompt`, and what the model declares | waits up to 180 seconds |
| `label_masks(pixels, width, height, dataset)` | a typed array, its sides, and a dataset id | one mask per class |

A model's parameters are properties of the options object with the names `GET /models/<id>`
publishes; an unknown name is a `400`, not a silent default. A geometry parameter is one value:
`boxes: [[480, 290, 110, 360, 1]]`, never five repeated names. `decode` and `resizeBeforeSubmit` are
the client's own options and are never forwarded.

## What is different here

- A mask arrives as `{data: Uint8Array, width, height}`, row-major: `data[y * width + x]` is the
  pixel at `(x, y)`. The raw `rle` stays beside it.
- A counting answer's `density.grid` is an array of rows; a backbone's `features.grid` is one flat
  `Float32Array` of `rows * columns * dim` values.
- A generated picture is `image.picture`, a `Uint8Array` of the encoded file; `image.data` remains
  the base64 string.
- `ApiError` carries `status`, `code` and the API's own `message`; nothing is retried for you.

## Next

- [Overview](index.md) — the shared methods and calling conventions
- [Python](python.md) · [Java](java.md) — the same calls in the other clients
- [Jupyter quickstart](../../examples/quickstart.md) — the same API, one step at a time
