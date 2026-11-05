# Clients Rules

Package: `clients/`. Shared constraints are in [`project.md`](project.md); distribution is in
[`docs.md`](docs.md).

- **Ship files, not packages published to an index.** Keep code, tests and each language's environment
  under its own directory. Python and Java ship one file; JavaScript ships its core and two platform
  entries. Python uses NumPy, requests and Pillow; Java uses the JDK; the JS core has no dependency.
  [Client design](../references/2026101722_clients-instead-of-curl.md).
- **Keep JS platform-specific resizing out of the core.** The web entry uses canvas; the Node entry
  uses sharp. Only the Node entry may require sharp from participants, so browser users do not
  inherit a Node dependency. [Resize work](../references/2026102016_client-resize-cost.md).
- **Do not restate response models.** Return dicts, objects or Maps from the API, adding decoded masks
  rather than replacing raw fields. Keep only route metadata shared in `routes.json`; the gateway
  contract test compares it against OpenAPI in both directions.
  [Contract test](../../gateway/tests/test_clients_contract.py).
- **Keep public method and parameter names identical across languages.** Only a decoded layer's
  container shape varies naturally: a mask as a NumPy array, `{data, width, height}` or a Java
  `Mask`, and a counting field as an array of rows.
  [API guide](../../docs/content/api/clients/index.md).
- **A new route or answer field lands with its clients.** A model id alone needs no new method —
  `predict` carries it — but a new route updates `routes.json` and is checked by the gateway contract
  test, and a new answer field needs its decoder in Python, JavaScript and Java in the same change. A
  client that silently drops a field is a client that disagrees with the server.
  [Contract test](../../gateway/tests/test_clients_contract.py).
- **A generation is a method on every client, with its own timeout.** `generate(model, prompt=…)`
  posts to `/models/{id}/generate` with the parameters in the query and no body. It waits up to
  180 seconds — the gateway's three-minute cap — while an ordinary call defaults to 60. The method
  decodes `image` like a predict answer; it does not invent a dataset or a row to make a generation
  look like one.
- **A picture answer is cut back to the frame it edits in the decoded layer.** `image.data` stays the
  model's own file, canvas included; `image.picture` is that file cropped to `frame.content` — in
  Python and Java in the client, in JavaScript by the browser or Node entry's `framer`, because the
  core has no image codec. A frame covering the whole picture is left untouched, so the common
  answer is not re-encoded. The crop never resizes: the answer keeps the model's resolution, and a
  caller overlays it by one scale. [Frame contract](../references/2026110610_nanobanana-frame-mapping.md).
- **Own RLE, palette and field decoding here, once per language.** Decode COCO column-major masks
  into row-major containers on receipt, retaining raw `rle` and other fields so area remains
  checkable; reshape a counting answer's `density.values` into `density.grid`, retaining `size` and
  `values` for the same reason; decode a backbone's base64 float16 grid into `features.grid`,
  retaining `size`, `dim`, `dtype` and `data`. A participant should not have to know which of the
  orders or encodings the wire uses. Docs, figures and `/play` call these implementations rather
  than duplicating them. [Decoder checks](../../clients/python/tests/test_geometry.py),
  [feature round-trip](../../clients/python/tests/test_client.py).
- **Read one key and one API origin.** `GLOBAL_WHEAT_API_KEY` and `GLOBAL_WHEAT_API` are environment
  defaults overridden by constructor arguments; fail construction without a key instead of waiting
  for the first `401`. [Python client](../../clients/python/global_wheat.py).
- **Send the token only to the configured API origin.** Public image and mask downloads carry no
  key, avoiding token disclosure in CDN logs. [Transport checks](../../clients/python/tests/test_client.py).
- **Publish copies only through the docs build.** `sync_clients.py` copies the five sources after
  building; `clients/**` must trigger that workflow. Do not add package publishing or another CI job.
  Python's `package = false` project and lock exist for local checks, not participant installation.
  [Publication](../../docs/scripts/sync_clients.py), [workflow](../../.github/workflows/docs.yml).
- **Run checks and live smoke before shipping relevant changes.** Use the commands in the
  [maintainer README](../../README.md#clients); Java checks use a real local HTTP server, and live
  smoke is opt-in. A file must work as downloaded, without a participant-owned build environment.
  [Smoke tests](../../clients/python/tests/test_live.py).
- **Do not assume cross-origin browser access.** The API and the dataset prefixes of the image
  distribution send no CORS headers. The `dev/` prefix carries a CORS response policy, but no page
  depends on it: the playground asks this host for its example photographs, and the host reads the
  objects. The web client serves API-origin pages, while Node scripts have no browser-origin
  restriction. A client change must not silently require opening CORS.
  [Playground](../../gateway/src/biohack_gateway/play.py).
