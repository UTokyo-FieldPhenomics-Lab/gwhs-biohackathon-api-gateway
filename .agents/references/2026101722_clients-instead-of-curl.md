# The clients: three files instead of curl, and every decoder inside them

Date: 2026-10-17 22:00 JST.
Topic: shipping `clients/` — one file each in Python, JavaScript, and Java — what it had to be to be
worth having, the two facts about the platform that changed the plan (CORS, and a raw GitHub URL's
content type), what was deleted with it, and what was run before anything was called done.

## Where this came from

[`2026101612_hf-inference-endpoints-and-a-client-package.md`](2026101612_hf-inference-endpoints-and-a-client-package.md)
recorded an importable client as **deferred**, with the reasoning that a client is a second definition
of the contract, and the shape it would have to take: "one module wrapping the two calls … importing
the response models rather than restating them. Not a package with its own `pyproject.toml`, lock
file and CI job." Seven weeks before the event, the call was to build it, and the constraint that
made it safe was different from the one sketched there: **no response models at all**, in any
language, because three languages cannot share Pydantic's.

## What decided the shape

- **The contract is frozen for the event** (`rules/project.md`). Drift is the usual reason a
  hand-written client rots; here it cannot, so the remaining risk is naming a route wrongly once.
  `clients/routes.json` is that list, and `gateway/tests/test_clients_contract.py` holds it against
  `app.openapi()` **in both directions** — a route added on one side and not the other fails.
- **A client that needs a build file is a client somebody integrates first.** So: one file per
  language, no package index, no lock file of the participant's. Python depends on `numpy` and
  `requests`; JavaScript on nothing; Java on the JDK, including its own small JSON reader.
- **The decoder belongs inside the client.** Every caller would otherwise learn COCO's column-major
  order to draw a mask. `mask` arrives decoded, row-major, and the raw `rle` stays beside it so the
  pixel count can still be checked against `area`.
- **Same method names in all three**, so the Clients page carries one table rather than three.
  Only the container differs: `(h, w)` NumPy array, `{data, width, height}`, `Mask`.

## The two platform facts that changed the plan

1. **A raw GitHub URL cannot be imported as a module.** `raw.githubusercontent.com` serves
   `content-type: text/plain` with `x-content-type-options: nosniff`, so a browser refuses
   `<script type="module" src="…/global-wheat.js">`. jsDelivr's GitHub channel does serve
   `application/javascript` with CORS — but a demo that depends on a third party's availability is
   worse than one that does not. So the site serves the file, and the download and import URLs are
   `https://docs.global-wheat.com/clients/…`.

   The copy lands in the **build output** — `site/clients/`, written by `docs/scripts/sync_clients.py`
   after `zensical build` and before the upload — rather than in `docs/content/clients/`. The page's
   URLs are absolute, so the file only has to be at that path once the site is built, and a copy under
   `docs/content/` would be a second copy of a source file, sitting in the source tree where somebody
   edits it by mistake. The script refuses to run before a build, because a build overwrites `site/`.
   What that costs: a local `zensical serve` preview has no downloads; check them with `zensical build
   && python3 docs/scripts/sync_clients.py && python3 -m http.server --directory site`.
2. **Neither `api.*` nor `data.*` sends `access-control-allow-origin`.** Checked:
   `curl -I https://data.global-wheat.com/gwfss/masks/00000000.png` answers no ACAO header, and the
   API answers none either — both planes are same-origin with the pages that use them by design. So a
   browser page on another origin can neither call the API nor read an image's pixels (a tainted
   canvas blocks `getImageData`). The JavaScript client is therefore **Node and the API's own
   origin**; the Clients page says so in the JavaScript tab instead of shipping a snippet that cannot
   run, and the palette-split section has two tabs, not three, for the same reason. The old
   Post-processing page's JavaScript tab fetched `mask_url` from another origin, which could not have
   worked.

## What was deleted

`docs/content/post-processing/` whole: `rle_decode.{py,js,kt}`, `label_masks.{py,js,kt}`, the page,
and its "What is worth optimizing" and "The files" sections. Its figures moved to
`docs/content/clients/`, and its prose is now the Clients page's "Reading a result", driven by client
calls rather than by raw requests. Kotlin went with it: a JVM implementation in Java is what both a
desktop JVM and an Android app can call, and two JVM copies would have been two decoders.

`docs/scripts/make_figures.py` now imports `clients/python/global_wheat.py` — one decoder, so the
figures cannot disagree with the library either.

## The measurements that moved out of the page

Recorded here because the page no longer carries them. One 512×512 palette mask and one 2509×592
object, NumPy and Node:

| job | approach | Python | JavaScript |
|---|---|---|---|
| palette mask | one pass per class | 17.3 ms | **1.7 ms** |
| palette mask | one pass over packed colours | **2.8 ms** | 2.8 ms |
| RLE | one assignment per run | 23.1 ms | **0.15 ms** |
| RLE | one cumulative sum over run boundaries | **4.5 ms** | — |

The shapes in the clients are the ones that won: NumPy vectorises the palette split and the boundary
cumsum; JavaScript's typed arrays already do that work, so there the four integer comparisons beat a
packed-colour lookup.

## Traffic, for the record

The site serves about 50 KB of client: `global_wheat.py` ~15 KB, `global-wheat.js` ~13 KB,
`GlobalWheat.java` ~25 KB. GitHub Pages' soft limit is 100 GB per month, assets answer
`cache-control: max-age=600` from Fastly (`x-cache: HIT`), and even ten thousand page loads that each
import the JavaScript file is ~130 MB — a fraction of a percent. The site is the right place for
these; it is not a place for datasets or weights, and Pages' terms say so.

## What was run

```
cd clients && uv sync && uv run pytest            # 19 passed, 1 skipped (the live one)
cd clients && uv run ruff check python tests && uv run mypy
cd clients/js && node --test tests/               # 12 passed
cd clients/java && javac -d /tmp/checks GlobalWheat.java tests/Checks.java && java -ea -cp /tmp/checks Checks
GLOBAL_WHEAT_LIVE=1 GLOBAL_WHEAT_API_KEY=… uv run --project clients/python pytest tests/test_live.py
cd gateway && uv run pytest                        # 174 passed
uv run --project docs zensical build --strict      # no issues
```

The three pages' snippets were run against the live API rather than reasoned about: the Python and
JavaScript end-to-end examples, the Java one plus its palette split, and each printed the same
numbers — `gwhs-temp` row 0's first object counts 12,074 pixels, which is its `area`, and `gwfss`
row 0 splits into 146,817 / 0 / 1,920 / 113,407 pixels for background, head, stem, and leaf.

## What broke, and what to do differently

- **Three gateway tests pinned the old decoder's name and the old transpose.**
  `test_app.py` asserted `post-processing/rle_decode.js` and
  `((index % height) * width + Math.floor(index / height)) * 4` — both correct for the design they
  described, both gone. They were rewritten to pin the new design (`clients/global-wheat.js`,
  `const at = index * 4;`, and `index % height` **absent**), which is the assertion that would catch a
  page re-introducing a transpose.
- **Java's JSON reader returned every number as a double**, so `dataset.get("rows")` printed `10.0`.
  Whole numbers now stay whole (a `Long`), fractional ones stay `Double`; the client's own ids and
  sizes are the case that decides this, and a participant printing a row count should not see a float.
- **`new URL(String)` is deprecated** in the JDK this was compiled with (26). `URI.create(...).toURL()`
  is the replacement, and Android is fine with it.
- **`List.of` and `InputStream.readAllBytes` are API 30/33 on Android.** The Java client avoids both
  — `Collections.singletonList` and a manual read loop — so the file works unchanged on the version of
  Android a participant actually has.
- **The live smoke test's first draft assumed every dataset's objects carry RLE.** `gwhd` is boxes:
  the assertion now reads `gwhs-temp` for geometry and `gwhd` for bytes. A fixture that happens to be
  one shape is how a test passes for the wrong reason.

## Where the pieces are

- `clients/routes.json` — the route list both sides are checked against.
- `clients/python/pyproject.toml` — `package = false`; it exists so the checks have an environment,
  not so anything can be installed. Each language directory is self-contained (file, checks, and that
  language's environment); `clients/routes.json` is the one shared file.
- `.agents/rules/clients.md` — the constraints this work produced.
- `docs/content/clients/index.md` — the participant-facing page, which is also the install guide.
