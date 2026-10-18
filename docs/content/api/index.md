# Calling the API

Every route on this site is one HTTP request: a key in one header, JSON in and JSON out. There are
three ways in, and they are the same API at three distances.

| | |
|---|---|
| [Raw HTTP](http.md) | the contract — every route on one line, the four calls worth copying, the refusals, the limits |
| [Base API reference](reference.md) | every route and every field of its answer, and a form that sends the call; served by the API itself |
| [Clients](clients.md) | the same API as a function in Python, JavaScript, or Java — one file each, masks decoded |

Start with **Raw HTTP** if you have never called this API: it is one page and it is the whole shape of
the thing. Go straight to **Clients** if what you want is a result rather than a request — an address
in, a decoded mask out. **Base API reference** is what you open while writing code, when you need the
shape of an answer. A *model's* parameters are not in that document, because they differ per model:
`GET /models/<id>` is the list, and [Model parameters](http.md#model-parameters) is how to send them.

Two things are true of all three:

- **A key is required, and it is issued to your team.** One key per team, three *inferences* in
  flight at once; over that the API answers `429` rather than queueing. A metadata read — a dataset
  listing, a row, the model directory — never takes one of those three. See
  [Limits](http.md#limits).
- **Bytes never come from `api.*`.** A response carries an absolute `data.*` address for its image or
  mask, which needs no key — so displaying an image is a CDN request, not one of your three.
