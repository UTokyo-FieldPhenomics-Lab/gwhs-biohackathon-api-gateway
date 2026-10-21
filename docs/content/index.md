# Start here

Build an agricultural application **without downloading the dataset, deploying a model, or owning a
GPU**. Two calls carry the whole thing: fetch one image, and get one model result.

## Three addresses

| address | what answers | key |
|---|---|---|
| `https://data.global-wheat.com` | the image and mask files themselves | none |
| `https://api.global-wheat.com` | datasets, annotations, model runs | every request |
| `https://docs.global-wheat.com` | this site | none |

`data.*` only serves static files, so no request reaches our gateway and there is nothing to abuse: it
answers without a key and caches for a long time. An `api.*` call spends gateway work and a share of a
GPU, so every one of them carries a key.

## What you can get

- **Three datasets** — `gwhd` (head boxes), `gwfss` (organ masks), `gwhs` (one mask per spikelet) —
  addressed as `(dataset, row)`. → [Datasets](datasets/index.md)
- **Two models** — `sam3.1-meta` for text, box or point prompts, and `gwfss-segformer-b4` for
  semantic organ masks. →
  [Models](models/index.md)
- **A client per language** — one file, downloaded rather than installed, which resolves an address,
  fetches the object, walks pages, and decodes masks. → [Clients](api/clients.md)

## Your first call

Keys are issued to teams: **one key per team, and one key may have three inferences in flight at
once** — reading a dataset listing or a row is never counted against them.
Ask the organisers for yours.

```bash
export API=https://api.global-wheat.com
export KEY=<your team's key>

curl -s -H "X-API-Key: $KEY" "$API/datasets"
```

The same call from a client is `client.datasets()`. The first ten rows of each published dataset
answer today; the full sets are published before the event.

## Where to go

- [Raw HTTP](api/http.md) — the contract, every route on one line, and the four calls
- [Base API reference](api/reference.md) — every route with the fields of its answer, and a try-it form
- [Clients](api/clients.md) — the same API as a function, in Python, JavaScript, or Java
- [Datasets](datasets/index.md) — what a row holds; one page per dataset
- [Models](models/index.md) — what the model does, on what hardware, and when to use your own
- [Live demo](examples/demo.md) · [Jupyter quickstart](examples/quickstart.md) — see it run, or run it yourself
