# Clients

A call is one HTTP request, and the [reference](reference.md) lists every one of them. What HTTP does
not do is get you from an address to pixels. That takes a call to resolve `(dataset, row)`, a second to
fetch the object from `data.*`, a cursor to walk a result larger than one page, and a compressed
`rle.counts` string before a mask is a mask.

So each of the site's three languages ships a **client**: downloaded rather than installed, with those
four things done for you. It carries no response models of its own — every answer is the API's own
JSON in the language's own container — so it cannot disagree with the server; it only saves you the
plumbing. JavaScript ships a core and two entries — a browser and a Node one — because the way an
image is made smaller belongs to the platform, and the core has no image code at all.

| language | file | what else you need |
|---|---|---|
| Python | `global_wheat.py` | `numpy`, `requests` and `pillow` |
| JavaScript, browser | `global-wheat.web.js` | nothing |
| JavaScript, Node | `global-wheat.node.js` | `sharp` — `npm i sharp` |
| Java | `GlobalWheat.java` | nothing — one `javac`, and it runs unchanged on Android |

## Load it

=== "Python"

    ```bash
    curl -O https://docs.global-wheat.com/clients/global_wheat.py
    ```

    ```python
    import global_wheat
    ```

    Put it beside your notebook, or in Colab `!curl -O` it into the working directory. There is
    nothing to install and no package index involved.

=== "JavaScript, browser"

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

    `.web.js` is the browser entry: it imports `global-wheat.js` beside it, which is the core — the
    routes, the parameters, and the decoders, with no image code — and adds a canvas that shrinks a
    photograph to the side the model runs at before it is sent. Nothing else is installed.

    **Where it runs matters.** In a browser, `api.global-wheat.com` sends no
    `access-control-allow-origin`, deliberately: a page on another origin cannot call the API, and
    it should not, because a token in a browser page is a token everyone can read. The one browser
    origin that works is the API's own, which is how the [live demo](../examples/demo.md) is built;
    anything else goes through your own server.

=== "JavaScript, Node"

    ```bash
    curl -O https://docs.global-wheat.com/clients/global-wheat.node.js \
         -O https://docs.global-wheat.com/clients/global-wheat.js
    npm i sharp
    ```

    ```js
    import {Client} from "./global-wheat.node.js";
    ```

    Both files have to sit together, because the entry imports the core beside it. In Node that
    entry is the whole surface — every call below works — and `sharp` is the one dependency any
    client has: it applies the photograph's EXIF orientation and shrinks it to the side the model
    runs at before the upload. Node 18 or newer supplies `FormData` and `Blob`, so nothing else is
    installed.

=== "Java"

    ```bash
    curl -O https://docs.global-wheat.com/clients/GlobalWheat.java
    javac GlobalWheat.java
    ```

    Drop the file into your source tree and call it — no dependency, no build file. On Android,
    declare the `INTERNET` permission and call the client off the main thread, which is where the
    network belongs anyway.

## One key

The client reads its token from the environment, so nothing has to be pasted into a notebook:

```bash
export GLOBAL_WHEAT_API_KEY="the key your team was given"
```

`Client()` picks that up. `GLOBAL_WHEAT_API` overrides the host, which is only useful while
something is being tested; the default is `https://api.global-wheat.com`. The key is issued per team
by the organisers, and one key may have three *inferences* in flight — metadata reads are not
counted against it. See [Limits](http.md#limits).

## Methods

The names are the same in all three languages, so this is one table rather than three. What differs is
only the container a decoded layer arrives in — a mask as a NumPy array, a typed array or a `Mask`,
and a counting answer's field as an array of rows — which the
[dataset pages](../datasets/index.md) spell out.

| call | what it gives you |
|---|---|
| `Client()` | a connection; key and host from the environment |
| `datasets()` | the directory: one line per dataset |
| `describe(id)` | one dataset's contract, palette included; fetched once and remembered |
| `images(id, limit=…)` | one page of rows, with `next_cursor` |
| `iter_images(id, …)` | every row a filter selects, page by page |
| `image(id, row)` | one row's whole annotation, each object's `mask` decoded |
| `image_bytes(id, row)` | the image itself |
| `mask_bytes(id, row)` | the row's mask image, where the dataset ships one |
| `models()` / `model(id)` | the directory / one model, with the parameters it accepts |
| `predict(id, dataset, row, …)` | run a model on a published row |
| `predict_file(id, image, …)` | run a model on your own photograph, resized before it is sent |
| `label_masks(pixels, …, id)` | one mask per class, from a palette mask and the dataset's palette |
| `fetch(url)` | any `image_url` or `mask_url` a response carried, with no key sent |
| `ApiError` | one refusal: `status`, `code`, and the API's own `message` |

Filter pairs are ordinary keyword arguments — `images("gwhd", split="train")` — and repeating a key
narrows the result rather than widening it, exactly as the API defines it. A model's parameters are
passed the same way, by their own names, and are encoded for you:
`predict("sam3.1-meta", "gwhd", 0, prompt="wheat head", confidence_threshold=0.5)`.

One difference is worth knowing, because it is the only place the two conventions meet. A **geometry
parameter is one value**: `predict("sam3.1-meta", "gwhd", 0, boxes=[[480, 290, 110, 360, 1]])` goes
out as one JSON array in one parameter, not as five `boxes=` names. Every client you download does
that encoding itself; if you are writing your own, [Raw HTTP § Model
parameters](http.md#model-parameters) is the contract. A repeated name is a dataset filter and means
something different there, which is why the second one is never generalised into the first.

An upload is resized before it is sent, by default, and the side belongs to the model rather than to
you: `imgsz` where you pass it and the model declares one, capped by the largest side that model was
run at, otherwise the model's published `resolution` — 1008 for `sam3.1-meta`, 512 for
`gwfss-segformer-b4`. The client fetches that declaration once and remembers it, so the resize is the
one the model would have made anyway. `resizeBeforeSubmit: false` (`resize_before_submit=False` in
Python) sends the original bytes untouched; the option is the client's own and is never forwarded as
a model parameter. An image already at or under that side passes through byte for byte, with no
re-encode and no loss of its EXIF. The core `global-wheat.js` carries no image code, so an upload
through it is sent as it is — use `global-wheat.web.js` or `global-wheat.node.js`.

A model that answers a **number** rather than geometry brings fields of its own: `count`, and for
the counting model `density` — the local-count grid that number is the sum of. The field is handed
to you already shaped, as `density.grid`: a NumPy array in Python, an array of rows in JavaScript, a
`double[][]` in Java. Nothing is left to reshape, and `density.size` and `density.values` stay beside
it — the raw cells, in the order the wire wrote them — so checking the grid against `count` is still
yours to do:

```python
answer = client.predict("tasselnetv4", "gwfss", 0, boxes=[[312, 143, 44, 55, 1]])
field = answer["density"]["grid"]          # rows × columns, cells summing to answer["count"]
```

`decode=False` in Python and `decode: false` in JavaScript leave a counting answer exactly as the API
wrote it, with no `grid`; the Java client always shapes it, as it always decodes a mask. If you read
`values` yourself, it is **row-major** — unlike the COCO counts a mask is decoded from, which are
column-major. [Counting results](http.md#counting-results) says what the number and the grid mean.

## When it goes wrong

A refusal is one body, `{"error": {"code": …, "message": …}}`, and every client raises it as one
error carrying the status, the code, and the API's own sentence — `ApiError` in Python and
JavaScript, `GlobalWheat.ApiError` in Java. The [status table](http.md#refusals) says what each code
means; nothing is retried for you.

## Next

- [Jupyter quickstart](../examples/quickstart.md) — every method once, in order, in a notebook you can run
- [Base API reference](reference.md) — what each method wraps
