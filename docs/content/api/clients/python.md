# Python client

One file, no package to install: `global_wheat.py` with `numpy`, `requests` and `pillow` already in
your environment. It is the same API the other two clients speak, with masks decoded to NumPy arrays.

## Load it

```bash
curl -O https://docs.global-wheat.com/clients/global_wheat.py
```

```python
import global_wheat
```

Put it beside your notebook, or in Colab `!curl -O` it into the working directory. There is nothing
to install and no package index involved.

## A runnable example

With `GLOBAL_WHEAT_API_KEY` set, this prints one row's size and runs one model on another row:

```python
import global_wheat

client = global_wheat.Client()          # key and host from the environment

row = client.image("gwhd", 0)
print(row["width"], "x", row["height"], "—", len(row["objects"]), "objects")

answer = client.predict("sam3.1-meta", "gwhs-temp", 0, prompt="wheat head")
print(answer["model"], "at", answer["resolution_used"], "px")
for found in answer["objects"]:
    print(found["idx"], round(found["score"], 3), int(found["area"]))
```

## Calls and parameters

The full method list is on the [overview](index.md#methods); these are the calls and options you
will use most:

| call | what you pass | notes |
|---|---|---|
| `Client(key=None, base=None, timeout=60)` | a token, or the environment | construction fails without a key |
| `image(dataset, row, decode=True)` | an address | `decode=False` leaves `rle` as the wire wrote it |
| `images(dataset, limit=100, cursor=None, **filters)` | filters as keyword arguments | the page reports `next_cursor` |
| `iter_images(dataset, limit=100, **filters)` | the same filters | walks every page for you |
| `predict(model, dataset, row, **params)` | the model's own parameters | `decode=False` keeps raw fields |
| `predict_file(model, image, **params)` | a path, `Path` or `bytes` | `resize_before_submit=False` sends the original bytes |
| `generate(model, **params)` | `prompt`, and what the model declares | waits up to 180 seconds |
| `label_masks(pixels, dataset)` | a palette mask image and a dataset id | one boolean array per class |

A model's parameters are keyword arguments with the names `GET /models/<id>` publishes; an unknown
name is a `400`, not a silent default. A geometry parameter is one value:
`boxes=[[480, 290, 110, 360, 1]]`, never five repeated names.

## What is different here

- A mask arrives as a `numpy.ndarray` of shape `(height, width)`, dtype `bool`; the raw `rle` stays
  beside it, so `mask.sum()` can be checked against the object's `area`.
- A counting answer's `density["grid"]` is a NumPy array of rows; `density["size"]` and
  `density["values"]` remain in wire order.
- A backbone's `features["grid"]` is a `(rows, columns, dim)` float32 array.
- A generated picture is `image["picture"]`, a Pillow image; `image["data"]` remains the base64
  string.
- `Client` is a context manager; `ApiError` carries `status`, `code` and the API's own `message`.

## Next

- [Overview](index.md) — the shared methods and calling conventions
- [JavaScript](javascript.md) · [Java](java.md) — the same calls in the other clients
- [Jupyter quickstart](../../examples/quickstart.md) — the same client, one step at a time
