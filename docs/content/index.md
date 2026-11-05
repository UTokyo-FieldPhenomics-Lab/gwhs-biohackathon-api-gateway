# Start here

Build an agricultural application **without downloading the dataset, deploying a model, or owning a
GPU**. Two calls carry the whole thing: fetch one image, and get one model result. The hosted Nano
Banana models answer a prompt with no image at all.

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

- **Datasets** — `gwhd` (head boxes) and `gwfss` (organ masks) are published in full; `gwhs` (one
  mask per spikelet) follows its annotation release, and `gwhs-temp` is the development sample. All
  are addressed as `(dataset, row)`. → [Datasets](datasets/index.md)
- **Nine models, thirteen ids** — text- and geometry-prompted segmentation, semantic organ masks,
  counting, feature backbones, prose and picture answers, and hosted image generation. →
  [Models](models/index.md)
- **A client per language** — one file, downloaded rather than installed, which resolves an address,
  fetches the object, walks pages, and decodes masks. → [Clients](api/clients/index.md)

## Your first call

Keys are issued to teams: **one key per team, and one key may have three inferences in flight at
once** — reading a dataset listing or a row is never counted against them.
Ask the organisers for yours.

```bash
export API=https://api.global-wheat.com
export KEY=<your team's key>

curl -s -H "X-API-Key: $KEY" "$API/datasets"
```

The same call from a client is `client.datasets()`. `gwhd` and `gwfss` answer every published row;
`gwhs` is not served until its annotation is released, and `gwhs-temp` is the development sample.

## Where to go

- [Raw HTTP](api/http/index.md) — the contract, every route on one line, and the five calls
- [Live API reference](api/reference.md) — every route with the fields of its answer, and a try-it form
- [Clients](api/clients/index.md) — the same API as a function, in Python, JavaScript, or Java
- [Datasets](datasets/index.md) — what a row holds; one page per dataset
- [Models](models/index.md) — what the model does, on what hardware, and when to use your own
- [Live demo](examples/demo.md) · [Jupyter quickstart](examples/quickstart.md) · [Agent skills](examples/agent-skills.md) — see it run, run it yourself, or hand it to your agent
