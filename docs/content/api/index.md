# Calling the API

The same API at three distances. Pick the one that matches what you are doing.

| way in | start here when | you get |
|---|---|---|
| [Raw HTTP](http/index.md) | you want to see the whole API on one page | routes, curl calls, refusals and limits |
| [Live API reference](reference.md) | you know the call and need its fields | every route and answer field, with a try-it form |
| [Clients](clients/index.md) | you want pixels rather than JSON | one file per language; images, paging and decoding done |

All three use the same team key: **one key per team, three *inferences* in flight at once**, and
`429` rather than a queue beyond that. A metadata read — a dataset listing, an image, the model
directory — takes none of the three. [Limits](http/index.md#limits).

Dataset images and masks are public `data.*` URLs and need no key, so displaying one is a CDN request
rather than an inference. Uploads and generated pictures travel through `api.*`.

A *model's* parameters are not in the API reference, because they differ per model:
`GET /models/<id>` lists them with their defaults and bounds, [Model
parameters](http/index.md#model-parameters) explains how to send them, and the [model
pages](../models/index.md) explain what they do.
