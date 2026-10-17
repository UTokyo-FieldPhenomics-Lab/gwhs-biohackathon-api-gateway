# Raw HTTP

Every call is one HTTP request: a key in one header, JSON in and JSON out. Nothing has to be
installed, and every client on this site is a convenience over what is on this page.

## The contract

| | |
|---|---|
| `https://api.global-wheat.com` | everything dynamic: the datasets, their annotations, model runs |
| `X-API-Key: <your team's key>` | on every request except `/health` |
| `https://data.global-wheat.com` | the bytes — an `image_url` or `mask_url` in a response, no key |

A response that points at an image carries an absolute `data.*` address: the gateway never carries
bytes, so displaying an image is a request to the CDN and not to us.

## Every route

| call | what it is |
|---|---|
| `GET /health` | liveness and version — the one route without a key |
| `GET /datasets` | the directory: one line per dataset |
| `GET /datasets/<id>` | that dataset's contract: fields, filters, classes, palette |
| `GET /datasets/<id>/images` | a page of rows — addresses and counts, never the annotations |
| `GET /datasets/<id>/images/<row>` | one row's whole annotation |
| `GET /models` | the models, the parameters each accepts, and the machines behind them |
| `GET /models/<id>` | one model, with its parameters and its machines |
| `POST /models/<id>/predict` | run a model on a published row |
| `POST /models/<id>/predict/upload` | run a model on your own photograph |

The complete list — every parameter, every field of every answer, and a form that sends the call —
is the [Base API reference](reference.md). The table above is the map, not the manual.

## Four calls

```bash
# the directory
curl -s -H "X-API-Key: $KEY" "$API/datasets"

# one row's annotation
curl -s -H "X-API-Key: $KEY" "$API/datasets/gwhs-temp/images/0"

# a model on one published row: the gateway resolves the address, you never send a URL
curl -s -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"dataset": "gwhs-temp", "row": 0}' \
  "$API/models/sam3.1-meta/predict?prompt=wheat%20head"

# a model on your own photograph: the file itself, as the multipart field `file`
curl -s -H "X-API-Key: $KEY" -F "file=@photo.jpg" \
  "$API/models/sam3.1-meta/predict/upload?prompt=wheat%20head"
```

A page of rows reports `next_cursor`; send it back as `cursor` for the next page, and stop when it is
`null`. Filters are query parameters, and a key sent twice narrows the result rather than widening it:
`?split=train&split=val` means both, never either.

## Refusals

Every refusal is one body, whichever route produced it:

```json
{"error": {"code": "invalid_request", "message": "row 99999 is past the end of gwhd"}}
```

| status | code | what happened |
|---|---|---|
| `400` | `invalid_request` | the request's own fault: a row that does not exist, a parameter outside its range, bytes that are not an image. The `message` names what was refused. |
| `401` | `invalid_key` | the key is wrong, or missing |
| `404` | `not_found` | no such dataset or model |
| `413` | `payload_too_large` | an upload over 15 MB |
| `429` | `quota_exceeded` | three requests in flight already from this key; nothing was queued |
| `503` | `unavailable` | no machine is answering this model. Safe to retry |
| `504` | `upstream_timeout` | the model took too long. Safe to retry |

Nothing is retried for you. `503` and `504` are worth another call, `400` never is, and a client that
guessed would hide which of the two happened.

## Limits

| what | limit | over it |
|---|---|---|
| requests in flight, per key | 3 | `429`, not a queue |
| an uploaded image | 15 MB | `413` |
| an uploaded image, decoded | 50 megapixels | `400` |

The three in flight are three across the whole team, not three per person: one key is one team's
share of the pool.
