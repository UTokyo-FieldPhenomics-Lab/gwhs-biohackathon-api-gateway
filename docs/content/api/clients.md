# Clients

A call is one HTTP request, and the [reference](reference.md) lists every one of them. What HTTP does
not do is get you from an address to pixels. That takes a call to resolve `(dataset, row)`, a second to
fetch the object from `data.*`, a cursor to walk a result larger than one page, and a compressed
`rle.counts` string before a mask is a mask.

So each of the site's three languages ships a **client**: one file, downloaded rather than installed,
with those four things done for you. It carries no response models of its own — every answer is the
API's own JSON in the language's own container — so it cannot disagree with the server; it only saves
you the plumbing.

| language | file | what else you need |
|---|---|---|
| Python | `global_wheat.py` | `numpy` and `requests` |
| JavaScript | `global-wheat.js` | nothing |
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

=== "JavaScript"

    ```html
    <script src="https://docs.global-wheat.com/clients/global-wheat.js"></script>
    ```

    ```js
    import {Client} from "https://docs.global-wheat.com/clients/global-wheat.js";
    ```

    Either form: a plain `<script>` leaves `Client`, `ApiError`, `rle_decode`, and `label_masks` on
    `globalThis`, and a module import gives you the same four names.

    **Where it runs matters.** In Node it is the whole surface — every call below works. In a
    browser, `api.global-wheat.com` sends no `access-control-allow-origin`, deliberately: a page on
    another origin cannot call the API, and it should not, because a token in a browser page is a
    token everyone can read. The one browser origin that works is the API's own, which is how the
    [live demo](../examples/demo.md) is built; anything else goes through your own server.

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
by the organisers, and one key may have three requests in flight — see [Limits](http.md#limits).

## Methods

The names are the same in all three languages, so this is one table rather than three. What differs is
only how a mask is handed to you — a NumPy array, a typed array, or a `Mask` — which the
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
| `predict_file(id, file, …)` | run a model on your own photograph |
| `label_masks(pixels, …, id)` | one mask per class, from a palette mask and the dataset's palette |
| `fetch(url)` | any `image_url` or `mask_url` a response carried, with no key sent |
| `ApiError` | one refusal: `status`, `code`, and the API's own `message` |

Filter pairs are ordinary keyword arguments — `images("gwhd", split="train")` — and repeating a key
narrows the result rather than widening it, exactly as the API defines it. A model's parameters are
passed the same way, by their own names, and are forwarded verbatim:
`predict("sam3.1-meta", "gwhd", 0, prompt="wheat head", confidence_threshold=0.5)`.

## When it goes wrong

A refusal is one body, `{"error": {"code": …, "message": …}}`, and every client raises it as one
error carrying the status, the code, and the API's own sentence — `ApiError` in Python and
JavaScript, `GlobalWheat.ApiError` in Java. The [status table](http.md#refusals) says what each code
means; nothing is retried for you.

## Next

- [Notebook](../examples/notebook.md) — every method once, in order, in a notebook you can run
- [Base API reference](reference.md) — what each method wraps
