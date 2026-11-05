# Clients

A downloaded client per language that turns an address into pixels. It resolves `(dataset, row)` —
an image's address — fetches the image from `data.*`, walks pages, and decodes masks, counting grids and
feature grids into the language's own containers. Answers stay the API's JSON — the client adds decoded
fields, not a second schema.

| language | files | also needs | guide |
|---|---|---|---|
| Python | `global_wheat.py` | `numpy`, `requests`, `pillow` | [Python](python.md) |
| JavaScript, browser | `global-wheat.web.js` + `global-wheat.js` | nothing | [JavaScript](javascript.md) |
| JavaScript, Node | `global-wheat.node.js` + `global-wheat.js` | `sharp`, and an ES-module `package.json` | [JavaScript](javascript.md) |
| Java | `GlobalWheat.java` | JDK 17 or newer | [Java](java.md) |

Each guide has the download and run steps for that language. The methods below are the same
everywhere; only the container a decoded layer arrives in changes.

## One key

The client reads its token from the environment, so nothing has to be pasted into a notebook:

```bash
export GLOBAL_WHEAT_API_KEY='YOUR_TEAM_KEY'
```

`Client()` picks that up. `GLOBAL_WHEAT_API` overrides the host, which is only useful while testing;
the default is `https://api.global-wheat.com`. One key is one team's share: three *inferences* in
flight, metadata reads not counted. See [Limits](../http/index.md#limits).

## Methods

The names are the same in all three languages, so this is one table rather than three. What differs
is only the container a decoded layer arrives in — a mask as a NumPy array, a typed array or a
`Mask` — which each language page lists.

| call | what it gives you |
|---|---|
| `Client()` | a connection; key and host from the environment |
| `datasets()` | the directory: one line per dataset |
| `describe(id)` | one dataset's contract, palette included; fetched once and remembered |
| `images(id, limit=…)` | one page of images, with `next_cursor` |
| `iter_images(id, …)` | every image a filter selects, page by page |
| `image(id, row)` | one image's whole annotation, each object's `mask` decoded |
| `image_bytes(id, row)` | the image itself |
| `mask_bytes(id, row)` | the image's mask image, where the dataset ships one |
| `models()` / `model(id)` | the directory / one model, with the parameters it accepts |
| `predict(id, dataset, row, …)` | run a model on a published image |
| `generate(id, …)` | make a picture from a prompt, with no image of your own |
| `predict_file(id, image, …)` | run a model on your own photograph, resized before it is sent |
| `label_masks(pixels, …, id)` | one mask per class, from a palette mask and the dataset's palette |
| `fetch(url)` | any `image_url` or `mask_url` a response carried, with no key sent |
| `ApiError` | one refusal: `status`, `code`, and the API's own `message` |

## Parameters

Filters and a model's parameters are ordinary arguments, by their published names:
`images("gwhd", split="train")`, `predict("sam3.1-meta", "gwhd", 0, prompt="wheat head")`. Repeating
a filter narrows the result; an unknown model parameter is a `400`. `GET /models/<id>` is the list,
and the [model pages](../../models/index.md) explain what each one does.

A **geometry parameter is one value**: `boxes=[[480, 290, 110, 360, 1]]` goes out as one JSON array
in one parameter, not as five `boxes=` names. A repeated name is a dataset filter and means something
different there. The clients encode it for you; over plain HTTP, [Model
parameters](../http/index.md#model-parameters) is the contract.

An upload is resized to the side the model runs at, by default. The client reads that side from the
model's declaration once and remembers it, so `predict_file` sends the size the model would have made
anyway. An image already at or under that side passes through unchanged, EXIF included;
`resize_before_submit=False` (`resizeBeforeSubmit: false` in JavaScript) sends the original bytes.
The option is the client's own and is never forwarded as a model parameter.

`generate` takes no image: `generate("nano-banana-2.1", prompt="a wheat field at golden hour")`. It
waits up to 180 seconds, the gateway's cap for an image model, while an ordinary call defaults to 60.
To edit a picture you already have — a published image or your own file — use `predict` or
`predict_file`, where `prompt` says what to change.

## Decoded answers

| answer | what the client adds | detail |
|---|---|---|
| a mask | `object.mask`, row-major and the size of the image; `rle` stays beside it | [Datasets](../../datasets/index.md) |
| a count | `density.grid`, whose cells sum to `count`; `size` and `values` stay in wire order | [Counting](../http/index.md#counting-results) |
| a backbone | `features.grid` shaped `(rows, columns, dim)`; `embedding` stays as the API wrote it | [Backbone results](../http/index.md#backbone-results) |
| a picture | `image.picture`; `image.data` remains the model's own file | [The frame](../../models/nano-banana.md#the-frame) |

A picture answer is `image`. A Nano Banana edit that had to be given a canvas also carries `frame` —
the box that is your picture, as fractions of the answer. The clients crop `picture` to that box, so it
has the shape of the picture you sent, and leave `image.data` as the model produced it. The crop never
resizes, so the answer keeps the model's resolution. A frame that covers the whole picture is left
untouched.

`decode=False` in Python and `decode: false` in JavaScript leave an answer as the API wrote it; the
Java client always decodes.

## When it goes wrong

Every refusal is one body, `{"error": {"code": …, "message": …}}`, raised as one error carrying the
status, the code, and the API's own sentence — `ApiError` in Python and JavaScript,
`GlobalWheat.ApiError` in Java. The [status table](../http/index.md#refusals) says what each code
means; nothing is retried for you.

## Next

- [Python](python.md) · [JavaScript](javascript.md) · [Java](java.md) — setup and a first run per language
- [Jupyter quickstart](../../examples/quickstart.md) — every method once, in order
- [Live API reference](../reference.md) — what each method wraps
