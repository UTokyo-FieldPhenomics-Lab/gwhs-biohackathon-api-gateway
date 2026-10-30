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
  [API guide](../../docs/content/api/clients.md).
- **Own RLE, palette and field decoding here, once per language.** Decode COCO column-major masks
  into row-major containers on receipt, retaining raw `rle` and other fields so area remains
  checkable; reshape a counting answer's `density.values` into `density.grid`, retaining `size` and
  `values` for the same reason — a participant should not have to know which of the two orders the
  wire uses. Docs, figures and `/play` call these implementations rather than duplicating them.
  [Decoder checks](../../clients/python/tests/test_geometry.py).
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
- **Do not assume cross-origin browser access.** The API and image distributions do not send CORS
  headers; the web client serves API-origin pages, while Node scripts have no browser-origin
  restriction. A client change must not silently require opening CORS.
  [Playground](../../gateway/src/biohack_gateway/play.py).
