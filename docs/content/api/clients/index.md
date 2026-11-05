# Clients

A call is one HTTP request, and the [reference](../reference.md) lists every one of them. What HTTP
does not do is get you from an address to pixels. That takes a call to resolve `(dataset, row)`, a
second to fetch the object from `data.*`, a cursor to walk a result larger than one page, and a
compressed `rle.counts` string before a mask is a mask.

So each of the site's three languages ships a **client**: downloaded rather than installed, with
those four things done for you. It carries no response models of its own — every answer is the API's
own JSON in the language's own container — so it cannot disagree with the server; it only saves you
the plumbing. JavaScript ships a core and two entries — a browser and a Node one — because the way an
image is made smaller belongs to the platform, and the core has no image code at all.

| language | files | what else you need | guide |
|---|---|---|---|
| Python | `global_wheat.py` | `numpy`, `requests` and `pillow` | [Python](python.md) |
| JavaScript, browser | `global-wheat.web.js` + `global-wheat.js` | nothing | [JavaScript](javascript.md) |
| JavaScript, Node | `global-wheat.node.js` + `global-wheat.js` | `sharp`, and an ES-module `package.json` | [JavaScript](javascript.md) |
| Java | `GlobalWheat.java` | nothing — one `javac` | [Java](java.md) |

Each guide has the download command, a runnable example, and the parameters that language's syntax
uses. The methods below are the same everywhere; only the container a decoded layer arrives in
changes.

## One key

The client reads its token from the environment, so nothing has to be pasted into a notebook:

```bash
export GLOBAL_WHEAT_API_KEY="the key your team was given"
```

`Client()` picks that up. `GLOBAL_WHEAT_API` overrides the host, which is only useful while
something is being tested; the default is `https://api.global-wheat.com`. The key is issued per team
by the organisers, and one key may have three *inferences* in flight — metadata reads are not
counted against it. See [Limits](../http/index.md#limits).

## Methods

The names are the same in all three languages, so this is one table rather than three. What differs
is only the container a decoded layer arrives in — a mask as a NumPy array, a typed array or a
`Mask`, and a counting answer's field as an array of rows — which the
[dataset pages](../../datasets/index.md) spell out.

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
| `generate(id, …)` | make a picture from a prompt, with no image of your own |
| `predict_file(id, image, …)` | run a model on your own photograph, resized before it is sent |
| `label_masks(pixels, …, id)` | one mask per class, from a palette mask and the dataset's palette |
| `fetch(url)` | any `image_url` or `mask_url` a response carried, with no key sent |
| `ApiError` | one refusal: `status`, `code`, and the API's own `message` |

## Calling conventions

Filter pairs are ordinary keyword arguments — `images("gwhd", split="train")` — and repeating a key
narrows the result rather than widening it, exactly as the API defines it. A model's parameters are
passed the same way, by their own names, and are encoded for you:
`predict("sam3.1-meta", "gwhd", 0, prompt="wheat head", confidence_threshold=0.5)`.

A model that makes a picture instead of reading one has no row and no upload to name, so it has its
own call: `generate("nano-banana-2.1", prompt="a wheat field at golden hour", aspect_ratio="16:9",
image_size="1K")`. The parameters ride in the query string, exactly as they do on a predict; the
difference is that the client waits longer for this call, because the gateway may give an image
model up to three minutes rather than ten seconds. To edit a picture you already have — a published
row or your own file — use `predict` or `predict_file`, where `prompt` says what to change.

A picture answer is `image`, and one where the model had to be given a canvas is `image` plus
`frame` — the box that is your picture, as fractions of the answer. The clients cut the picture back
to that box for you, so `image["picture"]` (`image.picture` in JavaScript, the `picture` bytes in
Java) has the shape of the picture you sent, while `image["data"]` stays the model's own file. The
crop never resizes, so the answer keeps the model's resolution. [The
frame](../../models/nano-banana.md#the-frame) has the reasoning and the four lines that do it over
plain HTTP.

One difference is worth knowing, because it is the only place the two conventions meet. A **geometry
parameter is one value**: `predict("sam3.1-meta", "gwhd", 0, boxes=[[480, 290, 110, 360, 1]])` goes
out as one JSON array in one parameter, not as five `boxes=` names. Every client you download does
that encoding itself; if you are writing your own, [Raw HTTP § Model
parameters](../http/index.md#model-parameters) is the contract. A repeated name is a dataset filter and
means something different there, which is why the second one is never generalised into the first.

An upload is resized before it is sent, by default, and the side belongs to the model rather than to
you. It is `imgsz` where you pass it and the model declares one, capped by the largest side that
model was run at; otherwise it is the model's published `resolution` — 1008 for `sam3.1-meta`, 512
for `gwfss-segformer-b4`. The client fetches that declaration once and remembers it, so the resize is
the one the model would have made anyway. `resizeBeforeSubmit: false` (`resize_before_submit=False`
in Python) sends the original bytes untouched; the option is the client's own and is never forwarded
as a model parameter. An image already at or under that side passes through byte for byte, with no
re-encode and no loss of its EXIF.

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
column-major. [Counting results](../http/index.md#counting-results) says what the number and the grid mean.

A **backbone** brings a different pair of fields: `embedding`, the CLS vector as a list of floats,
and `features`, the patch grid. The grid is handed over shaped too, as `features.grid`: a
`(rows, columns, dim)` float32 array in Python, one flat `Float32Array` of `rows * columns * dim`
values in JavaScript, and a `GlobalWheat.Features` in Java. A 32 × 32 × 1024 grid would otherwise
arrive as 1024 small arrays. `features.size`, `dim`, `dtype` and `data` stay beside it, in wire
order, so the encoding is still checkable:

```python
answer = client.predict("dinov3-vitl16", "gwfss", 0, side=512)
grid = answer["features"]["grid"]          # rows × columns × dim, float32
patch = grid[12, 20]
cosine = (grid @ patch) / (np.linalg.norm(grid, axis=-1) * np.linalg.norm(patch))
```

The vectors are not unit length, so the division is what makes it a cosine. The same `decode` switch
applies; [Backbone results](../http/index.md#backbone-results) has the wire shape and the model page has the
recipes.

## When it goes wrong

A refusal is one body, `{"error": {"code": …, "message": …}}`, and every client raises it as one
error carrying the status, the code, and the API's own sentence — `ApiError` in Python and
JavaScript, `GlobalWheat.ApiError` in Java. The [status table](../http/index.md#refusals) says what each
code means; nothing is retried for you.

## Next

- [Python](python.md) — the file, a runnable script, and NumPy/Pillow containers
- [JavaScript](javascript.md) — browser and Node entries, and typed arrays
- [Java](java.md) — one file, `javac`, and `Mask`/`Features`
- [Jupyter quickstart](../../examples/quickstart.md) — every method once, in order, in a notebook
- [Live API reference](../reference.md) — what each method wraps
