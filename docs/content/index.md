# Start here

Fetch a wheat image and run a model on it, one API call each. No dataset to download, no model to
deploy, no GPU to own. The hosted Nano Banana models answer a prompt with no image at all.

## Pick a starting point

| if you want | start with | you need |
|---|---|---|
| a result without writing code | [Live demo](examples/demo.md) | a browser and a team key |
| a notebook, step by step | [Jupyter quickstart](examples/quickstart.md) | Colab, or Python with NumPy, Pillow and requests |
| the API in your own project | [Clients](api/clients/index.md) | a client file and a team key |
| your agent to use the API | [Agent skills](examples/agent-skills.md) | an agent that can fetch a URL |

## Get a key

Keys are issued to teams: **one key per team, and one key may have three inferences in flight at
once**. Reading a dataset listing or an image is never counted against them. Ask the organisers for
yours.

```bash
export API=https://api.global-wheat.com
export KEY='YOUR_TEAM_KEY'      # replace with the key the organisers sent you
```

The clients read the same key from `GLOBAL_WHEAT_API_KEY`.

## Your first call

```bash
curl -s -H "X-API-Key: $KEY" "$API/datasets"
```

A JSON object with a `datasets` list means you are in. `401` means the key is wrong or missing; the
[status table](api/http/index.md#refusals) names every refusal. `gwhd` and `gwfss` answer every
published image; `gwhs` is not served until its annotation is released, and `gwhs-temp` is the
development sample.

The same call from a client is `client.datasets()`.

## What you can get

- **Datasets** — `gwhd` (head boxes) and `gwfss` (organ masks) are published in full; `gwhs` (one
  mask per spikelet) follows its annotation release. Each image is addressed as `(dataset, row)`. →
  [Datasets](datasets/index.md)
- **Nine models, thirteen ids** — segmentation from text, boxes or points; semantic organ masks;
  counting; feature backbones; prose and picture answers; hosted image generation. →
  [Models](models/index.md)
- **A client per language** — downloaded rather than installed, which resolves an address, fetches the
  object, walks pages, and decodes masks. → [Clients](api/clients/index.md)
- **The contract** — every route on one page, and every answer field in the live reference. →
  [Raw HTTP](api/http/index.md) · [Live API reference](api/reference.md)

## The three addresses

| address | what answers | key |
|---|---|---|
| `https://data.global-wheat.com` | image and mask files | none |
| `https://api.global-wheat.com` | datasets, annotations, model runs | every data call |
| `https://docs.global-wheat.com` | this site | none |

Dataset images and masks are public CDN objects, so displaying one is not an API call and does not
use an inference. Uploads and generated pictures travel through `api.*`.
