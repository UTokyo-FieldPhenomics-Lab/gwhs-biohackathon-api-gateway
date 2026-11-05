# Python client

One file, downloaded rather than installed. Masks arrive as NumPy arrays and counting or feature
grids arrive shaped; the answer stays the API's JSON otherwise.

## Setup

Python 3.9 or newer, with the client's three dependencies:

```bash
python -m pip install numpy requests pillow
curl -O https://docs.global-wheat.com/clients/global_wheat.py
export GLOBAL_WHEAT_API_KEY='YOUR_TEAM_KEY'
```

In Colab the three packages are already there; only `!curl -O` the file. Put it beside your notebook,
or in the working directory you run from. Nothing is published to a package index.

## Run an example

Save this as `example.py` beside the client, then run `python example.py`:

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

`gwhs-temp` is the development sample of `gwhs`, so this call works before the full annotation is
released. On your own photograph, use `predict_file("sam3.1-meta", "photo.jpg", prompt="wheat head")`.

## Pass model parameters

A model's parameters are keyword arguments with the names `GET /models/<id>` publishes. An unknown
name is a `400`, not a silent default.

```python
sam = client.predict("sam3.1-meta", "gwhs-temp", 0,
                     prompt="wheat head", confidence_threshold=0.5)

counted = client.predict("tasselnetv4", "gwfss", 0,
                         boxes=[[312, 143, 44, 55, 1]])
```

A geometry parameter is one value — `boxes` or `points` as a list — never repeated keyword names. The
[model pages](../../models/index.md) say what each parameter does; [Model
parameters](../http/index.md#model-parameters) is the wire contract.

## Read decoded outputs

- A mask is `object["mask"]`, a `(height, width)` boolean array; `object["rle"]` stays beside it, so
  `mask.sum()` can be checked against `object["area"]`.
- A counting answer's `density["grid"]` is an array of rows; `density["size"]` and
  `density["values"]` remain in wire order.
- A backbone's `features["grid"]` is a `(rows, columns, dim)` float32 array.
- A picture is `image["picture"]`, a Pillow image. On an edit that carries `frame`, it is already
  cropped to that box — the shape of the picture you sent — while `image["data"]` remains the
  model's full file.
- `Client` is a context manager, and `GLOBAL_WHEAT_API` overrides the host.

## When it goes wrong

A refusal raises `global_wheat.ApiError` with `status`, `code` and the API's own `message`. Nothing is
retried for you: `429` means wait, `503`/`504` are worth one more call, and `400` needs the request
fixed. [Refusals](../http/index.md#refusals) lists every code.

## Next

- [Overview](index.md) — the shared methods and calling conventions
- [JavaScript](javascript.md) · [Java](java.md) — the same calls in the other clients
- [Jupyter quickstart](../../examples/quickstart.md) — the same client, one step at a time
