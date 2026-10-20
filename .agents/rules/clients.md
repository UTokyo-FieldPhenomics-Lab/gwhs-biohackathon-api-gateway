# Clients Rules

Package: `clients/`. Repo-wide invariants are in [`project.md`](project.md), and what the site does
with these files is in [`docs.md`](docs.md).

- **One directory per language, and one core file in it that ships.** `python/`, `js/`, `java/`: each
  holds its client, its checks, and whatever environment those checks need, so nothing about one
  language sits where another one has to look. Only `clients/routes.json` is shared, because it is
  the one fact all three restate. A participant gets a file and a first call; no package index of
  ours, no lock file of theirs, no build file. That is why the Python file depends on `numpy`,
  `requests` and `pillow`, the Java file on the JDK, and the JavaScript core on nothing at all — a
  client that needs a dependency resolver is a client somebody integrates before their first call,
  and this window is five days long.
- **JavaScript adds two entries beside its core, and only the entry knows a platform.** The core
  carries transport, parameters and decoders, and no image code. `global-wheat.web.js` resizes with
  the canvas the browser already has; `global-wheat.node.js` imports `sharp`, which is the one
  dependency any of these files may ask a participant for, and only on Node. The split exists
  because a browser cannot resolve `sharp` and Node has no canvas; putting either in the core would
  make one platform's tool a requirement of the other.
- **The clients carry no response models.** Every answer is the API's own JSON in the language's own
  container — a `dict`, a plain object, a `Map` — because a restated field is a field that can
  disagree with the server, in three languages at once. What the files do restate is the **routes**,
  and `routes.json` is that list: `gateway/tests/test_clients_contract.py` holds it against the
  application's own OpenAPI document in both directions, so a route added on one side and not the
  other fails the suite rather than a participant's first call.
- **The method names are the same in all three files**, and so are the parameter names, which is what
  lets the Clients page carry one table instead of three. Only the container a decoded mask arrives
  in differs — a `(height, width)` array, `{data, width, height}`, a `Mask` — and that is the one
  place a language is allowed to be itself.
- **A mask is decoded on the way in, and the raw `rle` is kept beside it.** The transpose from COCO's
  column-major order belongs to the decoder rather than to every page and notebook that draws a mask:
  one place to get it wrong instead of every caller. Keeping `rle` is what makes the answer checkable
  — the decoded pixel count is the `area` beside it — so the client adds `mask` and removes nothing.
- **RLE and palette decoding live here, one implementation per language, and nowhere else.** The
  documentation site publishes copies of these files rather than a decoder of its own, and the API's
  run-a-model page loads that same published file, so a demo, a tutorial, and a notebook cannot
  disagree about what a correct decode is.
- **The environment names one key and one host.** `GLOBAL_WHEAT_API_KEY` and `GLOBAL_WHEAT_API`; a
  constructor argument wins over either. A client with no key raises where it is built, because a
  `401` on the first data call sends a participant looking in the wrong place.
- **The token goes to the API's origin and nowhere else.** An `image_url` or `mask_url` is fetched
  with no key: `data.*` is a public distribution, and a token in a CDN's log is a token leaked.
- **Nothing here is published, and no workflow builds it.** PyPI and npm are out: the site serves
  these five files, and `docs/scripts/sync_clients.py` is what puts them there. There is no new CI
  job, and `clients/python/pyproject.toml` exists only to give the checks an environment
  (`package = false`); its `uv.lock` pins what the checks import — `numpy`, `requests`, `pillow`,
  `pytest`, `mypy`, `ruff` — and never becomes something a participant installs, because they
  download the file, and `docs/scripts/sync_clients.py` is what puts it in the built site.
- **A change here is run, not reasoned about.** The Python checks are `uv run --project clients/python
  pytest`, the JavaScript ones `node --test tests` from `clients/js`, and the Java ones `javac` plus
  `tests/Checks.java` — a local HTTP server rather than a stub. The live smoke test,
  `GLOBAL_WHEAT_LIVE=1`, calls every method once against the real API; a client that ships has been
  run against it.
- **The JavaScript client is for Node, and for a page the API itself serves.** A page imports
  `global-wheat.web.js`, a script imports `global-wheat.node.js`; both import the core beside them.
  Neither `api.*` nor `data.*` sends an `access-control-allow-origin` header — the reference and the
  run-a-model page are same-origin on purpose — so a browser page on another origin can call neither
  the API nor read an image's pixels, and a token in such a page would be public anyway. Opening
  CORS is a decision for the API's own rules, not something a client may assume.
