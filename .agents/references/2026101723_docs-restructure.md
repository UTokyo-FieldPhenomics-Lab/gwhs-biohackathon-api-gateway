# The site, restructured around what a participant asks

Plan: [`../plans/2026101723_docs-restructure.md`](../plans/2026101723_docs-restructure.md). Rules that
bind the result: [`../rules/docs.md`](../rules/docs.md).

## What the site is now

Twelve pages in six chapters, each answering one question, in the order a participant asks them:

```
Start here          index.md
Calling the API     api/http.md · api/clients.md · api/reference.md
Datasets            datasets/index.md · gwhd · gwfss · gwhs
Models              models/index.md · sam3.1-meta
Examples            examples/demo.md · examples/notebook.md
```

- 718 lines across five pages became ~800 across twelve: the longest page went from 283 lines to 140,
  and no page now teaches a request that another page already taught. The `curl` of a dataset row
  appeared on four pages; it appears once.
- A capability page shows the **response** and one line that produces it; anything with a second step
  — pagination, fetching bytes, decoding, drawing — lives in `examples/`. That is what stopped
  Datasets and Models from turning into tutorials.
- The nav is `Calling the API` (3 pages) and `Examples` (2), with `Datasets` and `Models` as sections
  whose landing page is their `index.md`; six rows closed, sixteen open.
- `docs/content/clients/` is gone with its figures: the page is `api/clients.md`, the palette and RLE
  figures are on the dataset pages they describe.

Deleted from the prose, not relocated: every sentence about how the site is built — the handwritten
`/models` snapshot and the sentence that apologised for it, "that is the resolution, not the model",
the repeated `computed_by` paragraph, the browser `fetch` example that CORS makes unusable for a
participant. That material is in `.agents/`, where it belongs.

## The notebook is generated

`docs/content/examples/notebook.md` is the source; `quickstart.ipynb` beside it is written by
`docs/scripts/make_notebook.py` before every build, and committed because Colab's only input is a
GitHub path (`colab.research.google.com/github/<owner>/<repo>/blob/<branch>/<path>`) — verified, there
is no URL input box in Colab's *Open notebook*. `git diff --exit-code` after the build is what notices
a committed copy that the page has moved past; the script itself has one behaviour, overwrite.

The converter refuses what a notebook cannot carry, by name: a fence that is not `python` or `bash`, a
language tab, an admonition, an `export` inside a shell cell, an unclosed fence. Content between
`<!-- notebook: skip -->` and `<!-- /notebook: skip -->` stays on the page — that is where the three
buttons live.

## What broke, and what it taught

1. **A relative path is rebased against the *source* directory, not the URL depth.** Zensical resolves
   a markdown link or a raw `src` as if the page were a file in `docs/content/<dir>/`, then re-emits it
   relative to the directory-style URL. So `examples/demo.md` writes `demo.js` and
   `../clients/global-wheat.js`, not `../demo.js` and `../../clients/…`; the first attempt rendered
   `../../../clients/global-wheat.js` and would have 404'd. Eight image paths on the dataset pages had
   the same bug (`../gwhd-row0-boxes.jpg` for a file in the same directory). The check that catches it
   is not `--strict` — it validates pages, not assets — but a scan of the built HTML against the built
   tree: 0 broken after the fix.
2. **`describe()` has no `geometry`.** The directory entry carries `geometry` (`boxes`, `rle`) or
   `mask.kind` (`semantic`); the dataset contract carries `filters`, `splits`, `classes`, and no
   geometry at all. The notebook's step 3 was written from the directory's shape and raised `KeyError`
   on the first live run.
3. **Moving the recorded answer broke three test suites.** `example-prediction.json` moved from
   `docs/content/models/` to `docs/content/examples/`, and each client's geometry check pins that path
   as a constant — Python, JavaScript and Java alike. The fixture is shared, so its move is a
   three-file edit; the alternative (a copy per language) is what the repository keeps refusing.
4. **A section's landing page needs the section's own title, and only `index.md` collapses.** Listing
   `api/http.md` as "Calling the API" under a section of the same name printed the label twice — there
   is no `api/index.md`, so that page is a child titled "Raw HTTP". `datasets/index.md` and
   `models/index.md` do collapse into their section links, which is what `navigation.indexes` is for.
5. **`api/frame.css` had one rule and it stopped being true.** It hid the `<h1>` of a page that was
   nothing but a frame; `api/reference.md` now opens with two paragraphs about what the reference is,
   so the rule was hiding a heading the page needs. The file is deleted and `extra_css` with it;
   `frame.js` sizes both frames, unchanged.

## Verified

- `make_notebook.py` → 26 cells; the page's ten sections, in order.
- Every code cell in the generated notebook ran against the live API (one runner executing the cells
  with the token in the environment, skipping the `getpass` cell and the `!curl` one):
  `592 x 2509 — 24 objects`, first object `spikelet_-_side1 [231.0, 923.0, 126.0, 162.0]
  ['Green-Spikelet']`, `(2509, 592) bool 12074 12074.0`, `sam3.1-meta at 1008 px … 1 objects
  0 0.52 173716`, `background 146817 · head 0 · stem 1920 · leaf 113407`, and `404 not_found no such
  row` from the deliberate failure.
- `zensical build --strict`: no issues. All twenty built URLs answer `200` from a static server, and
  the client files are byte-identical to `clients/`.
- `clients/python` 19 passed + 1 skipped, `node --test` 12 passed, `javac` + `Checks` all checks
  passed, `gateway` 174 passed.

## Live facts this page set in prose

`GET /models` on 17 October 2026 reported **three** backends — `lab-5090`, `lab-3090`, and
`homepc-4090`, all answering `sam3.1-meta` — so the pool table on `models/index.md` lists three rows,
and `rules/backend-pool.md` stopped saying the gateway does not route to the home machine. The
predictions in the notebook came back from `lab-5090`, which is why its `area` (173,716) is the
`gwhs-temp` row-0 figure from that card rather than the 3090's 173,777 quoted on the model page: one
answer is one machine's answer, and `computed_by` is where that is written down.

## 18 October: the panel that never loaded

The recorded-answer canvas had drawn nothing since it was written. `clients/js/global-wheat.js` ends
with `export`, so the classic `<script src="…">` that both the demo page and `/play` used failed with
`SyntaxError: export declarations may only appear at top level of a module`, and every later reference
died with `GlobalWheat is not defined`. The empty bordered box that made it look like a slow load was
CSS: Material styles `figure` with `display: flow-root`, which beats `[hidden]`'s `display: none`, so
the "hidden" panel still occupied a frame.

Nothing caught it. The gateway test asserted the `src` string and not the tag, the docs build never
executes JavaScript, and the page's own decoder guard sits inside the script that never ran.

- **Replaced, not repaired.** The panel was the site's only JavaScript, and what it demonstrated — a
  decoded mask, no key needed — a generated figure shows as well. `known-good.jpg` is drawn by
  `make_figures.py` from the same record the three client suites decode, so the picture, the scores on
  it, and the numbers in the caption share one source. `example-prediction.json` moved to
  `clients/fixtures/`: only tests read it now, so it is no longer published.
- **A call we know works** took its place: `gwfss` row 4, `prompt="wheat head"`,
  `confidence_threshold=0.25` → 18 objects, `Σ mask = Σ area = 109,419 px`, answered by `lab-3090`.
  That is a test case a participant can run against their own setup.
- **Fixed where it mattered.** `/play` loads the client as `type="module"` (three tags, in document
  order, so the decoder has run before the page draws); `api_key.js` publishes its store through
  `globalThis` because a module is strict and cannot assign to an undeclared name; and `test_app.py`
  now asserts `type="module"`, because the tag is what failed.
- **The deployment was two bugs deep.** The live `/play` was older code that still pointed at
  `post-processing/rle_decode.js`, deleted the previous week — a page that loads a file from another
  repository can be broken by that repository's changes. Redeploying to the commit that used
  `clients/global-wheat.js` is what exposed the module bug underneath.
