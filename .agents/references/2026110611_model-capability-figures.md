# Model capability figures under At a glance

Date: 2026-11-06. The model pages had a table and, on three pages, one result figure. A reader could
not see what most models actually answered until deep in `What comes back`, and the two semantic
pages gave no way to tell a request/answer/reference card from a multi-mode card. This pass gives
every model page one capability figure under `At a glance`, with a role tag on every panel, and moves
the site's page assets into one tree.

## The two commits

- `b0cb382 docs(assets): move page assets under static/` — the mechanical asset move.
- The model figures and this record share the next commit; the evidence below is that change's.

## The asset move

`docs/content` now keeps prose and one asset tree:

```
docs/content/
├── api/ datasets/ examples/ models/     pages
└── static/
    ├── styles/  footer.css, output.css, typography.css
    ├── scripts/ frame.js
    └── images/  datasets/, models/, examples/
```

- `static/` rather than `assets/`: Zensical emits the theme to `site/assets/` (its default favicon is
  `assets/images/favicon.png`), so a source folder with that name would mix the two in the build.
- `zensical.toml` points `extra_css` and `extra_javascript` at the new paths.
- `make_figures.py` writes `static/images/datasets` and `static/images/models`;
  `record_notebook.py` writes `static/images/examples` and returns a `../static/images/examples/…`
  link. `quickstart.ipynb` stayed in `examples/` — it is a page download, and its pictures are
  embedded outputs, so the move does not touch it.
- All 18 image links and the four global files moved; `zensical build --strict` passed, and the
  generated notebook was unchanged after `make_notebook.py`.

## The cards

Every panel carries a small grey role tag above its bold title. The tag is the card's type, not the
model's:

| tag | page | meaning |
|---|---|---|
| `MODE` | `sam3.1-meta` | four uses of one model on one image |
| `INPUT` / `ANSWER` / `REFERENCE` | `gwfss-segformer-b4`, `sprout-l-gwfss` | the request, the answer, the published annotation |
| `INPUT` / `DERIVED` | `dinov3` | the request, and a visualization derived from the answer |
| `REQUEST` / `ANSWER` | `tasselnetv4`, `wevisone-vl-4b` | a prompt/exemplar request, and its answer |
| `MODEL` | `yolo` | two ids of one family |
| `ONE CALL` | `internvl-u`, `nano-banana` | one request/answer call drawn as a conversation |

The generative cards use `REQUEST` and `ANSWER` bubbles. They are one call, not a session; the
caption says so. Prose answers are repeated in the caption for search and screen readers.

## Calls and observed answers

All calls were made through `https://api.global-wheat.com` with a team token; the key was read from
the environment and never printed.

| page | call | answer |
|---|---|---|
| `sam3.1-meta` | `gwhd` image 4, `seed head`, `confidence_threshold=0.31` | 44 objects |
| `sam3.1-meta` | same, plus box `[459, 297, 86, 154, 1]` | 62 objects |
| `sam3.1-meta` | box alone | 65 objects, `category: null` |
| `sam3.1-meta` | point alone at `[502, 374, 1]` | 62 objects, `category: null` |
| `gwfss-segformer-b4` | `gwfss` image 4, recorded fixture | three class masks |
| `sprout-l-gwfss` | `gwfss` image 4, recorded fixture | three class masks |
| `tasselnetv4` | `gwhd` image 4, the live demo's three exemplars | count 41.4, field 23 × 23 |
| `dinov3-vitl16` | `gwfss` image 4, `side=512` | grid 32 × 32 × 1024, float16 |
| `wevisone-vl-4b` | `gwhd` image 4, `wheat head` | 43 instances, no score |
| `yolo26-ultralytics` | `dev/potted-plant.jpg`, `imgsz=1024`, threshold 0.25 | one `potted plant`, 0.77 |
| `yolo26-seg-ultralytics` | same photograph | one `potted plant`, 0.78, with a mask |
| `internvl-u` | `gwfss` image 4, `mode=text` | one-sentence description |
| `internvl-u` | same, `mode=image`, cooler light, `size=768` | 768 × 768 edit |
| `internvl-u` | `mode=text_image`, one spike prompt, `size=768` | thought plus 768 × 768 picture |
| `nano-banana-2.1` | `generate`, “a wheat field at golden hour”, 16:9, 1K | 1376 × 768 |
| `nano-banana-2.1` | `edit` `gwfss` image 4, cooler light, 1K | 1024 × 1024 |

The two Nano Banana calls spend credits and sample anew, so they were recorded once into
`docs/scripts/fixtures/nano-banana/` by `record_nano_banana.py`. The fixture holds JPEG copies (600 KB
for the pair, down from 3.4 MB of PNG) and the prompts; `make_figures.py` never calls the paid model.
`--force` is the only path that spends again.

## What broke, and what changed

- **The YOLO card was unreadable on carrots.** Three or four overlapping boxes stacked their labels
  on one another, and the seg title `yolo26-seg-ultralytics — boxes + masks` clipped at the right
  edge. The card now uses `dev/potted-plant.jpg`, which answers exactly one object from both ids, and
  the title is `yolo26-seg — boxes + masks`; the full id is in the caption and the page prose.
- **The point marker was too small.** It was drawn on the 1024 px answer and then shrunk to 420.
  Prompt marks are now drawn on the panel at the panel's own scale; the point is a 10 px crosshair
  with its label, and the box is a 4 px outline.
- **The DINOv3 patch-similarity panel did not earn its place.** Seven candidate patches all produced
  a smooth blue/yellow map that did not separate heads from straw at 32 × 32. The page already
  teaches the cosine recipe in `What you can do with it`; the capability card stays at two panels,
  `INPUT` and `DERIVED`, and the PCA remains the visual.
- **The wevisone count appeared twice.** The title already said `43 instances · no score`; the
  corner label repeated it and was removed.
- **The first semantic cards had no input panel.** Both now show the photograph, the model's classes
  and the dataset's mask, with all three roles tagged.
- **The internvl `mode=image` answer is a blue, grid-like frame.** It is the model's own answer to
  “colder evening light” at 768; it was kept rather than replaced with a prompt that matched the page
  less closely. The `mode=text_image` answer is cleaner and shows the reasoning line above the
  picture.

## Verification

```
# figures
GLOBAL_WHEAT_API_KEY=... uv run --project docs --with pillow --with numpy --with requests \
  python docs/scripts/make_figures.py

# the model pages' parameter tables still match the recorded directory
python3 docs/scripts/make_models.py && git diff --exit-code -- docs/content/models/

# the site
uv run --project docs zensical build --strict

# the notebook still converts
uv run --project docs python docs/scripts/make_notebook.py
```

The figure run wrote the dataset figures and nine model cards under `docs/content/static/images/`;
the model pages reference the nine cards. `make_models.py` produced no diff. `zensical build --strict` reported no issues, and
the notebook converter wrote 42 cells with no diff. The page images resolve as
`../../static/images/models/…` from the built pages.

## Follow-ups

- The `internvl-u` page still quotes 0.67 s for prose and 11–15 s for a picture. The first picture in
  this pass was answered from the gateway cache; a 3090 measurement in
  [`202611060235_lab-intel3090-internvl.md`](202611060235_lab-intel3090-internvl.md) reports about
  49.5 s. The page was not re-timed in this change; that is a separate measurement.
- `record_notebook.py` was not run for this change. Its new output path and returned link were
  checked by reading the code and by the converter/build; the next quickstart recording will be the
  first live exercise.
- The layer scrollbars remain pinned to the muted foreground; the asset move did not touch that.
