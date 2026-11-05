# The quickstart in three parts

The page had ten flat steps and a model table. Only `sam3.1-meta` was executed; the other eleven ids
were a list to copy, so the third part of the learning path — the one a participant came for — had no
result on the page. The page also asked a beginner to hold ten step numbers in mind while the actual
work was three moves: set up, read a dataset, run a model.

## What changed

- **Three sections.** `## 1. Setup`, `## 2. Datasets`, `## 3. Models`; the subsections are H3 and
  unnumbered. The notebook is still one file, `quickstart.md`, and `quickstart.ipynb` is still
  generated from it.
- **Part 1 covers the environment, not only the file.** It states the local
  `pip install numpy pillow requests` (inline, because a bash fence would run during recording) and
  prints the three versions beside the client import. The key cell is unchanged.
- **Part 2 shows a page, not only an image.** It lists the datasets, reads the declared filters,
  fetches a filtered page, then takes `gwhs-temp` image 0 apart: detail, picture, decoded mask, area
  check, overlay. The palette section moved to `gwfss` image 4, whose annotation has all three
  foreground classes; image 0 has no `head` pixels and printed `head 0 px`.
- **Part 3 runs four answer shapes.** `sam3.1-meta` on `gwhd` image 4 (`seed head`, threshold 0.31,
  44 objects); `gwfss-segformer-b4` on `gwfss` image 4 (three class masks); `tasselnetv4` on `gwhd`
  image 4 with the live demo's three exemplars (count 41.4, density 23 × 23); `dinov3-vitl16` on
  `gwfss` image 0 (embedding 1024, features 32 × 32 × 1024, float16), with the first three principal
  components mapped to red, green and blue and tinted over the photograph. The promptable and counting
  models share the `gwhd` plot; the backbone's figure is its feature grid, not an object mask.
- **The rest of the catalogue stays a table**, with links to each model page. `internvl-u` was not
  answering on the recording day, and a Nano Banana generation spends credits, so neither can be a
  recorded cell. The table says why it is not run.
- **Seven pictures replace the two tall ones.** Four model answers at 400 × 400 (promptable,
  semantic, counting, backbone PCA), the palette mask at 320 × 320, and the two `gwhs-temp` pictures
  at 118 × 500. The page is longer in cells (19 recorded) but each picture is bounded.

## The recorder now names a picture after its heading

`record_notebook.py` used `## N.` to derive `quickstart-step<N>.png`. With three numbered parts and
several pictures under one part, that would have named every Part 2 picture `step2*.png` and repeated
"What step 2 returns" as every alt text. `HEADING` now matches H2–H4, `splice` carries the nearest
label, and `picture` writes `quickstart-<slug>.png` with the heading as alt text. The stale-file glob
is `quickstart-*.png`; the first run removed the two `quickstart-step5/6.png` files.

## What broke on the way

- **The first density drawing was a yellow wash.** Normalising by the field's maximum let one corner
  cell dominate; every other cell mapped to almost full green under a constant red channel. The fix is
  a 99th-percentile clip of occupied cells and an RGBA layer whose alpha is the density, composited
  over the photograph, so an empty cell changes nothing. The second attempt is the committed figure.
- **The first counting image was `gwhd` image 1.** It has 12 annotated heads and the model answered
  7.5, but the page's own list shows that count, so the section invited a comparison the model page
  says not to make. The live demo's example — `gwhd` image 4, three exemplars — is the better demo:
  the same image as the promptable segmenter, and 41.4 against 57 annotated heads. The text now says
  the count is the model's estimate, not the annotation's.
- **The first `sam3.1` demo was the old one.** `gwhs-temp` image 0 with `wheat head` answered one
  object. The replacement uses the model page's measured case, `gwhd` image 4 with `seed head` at
  0.31, which answers 44 and draws a useful mask.

## Verification

```
python3 docs/scripts/make_models.py
python3 docs/scripts/make_notebook.py                  # 42 cells, 21 code, no outputs
uv run --project docs zensical build --strict          # No issues found
git diff --check                                       # clean
```

The recorder ran: `recorded 19 cells, 7 pictures onto quickstart.md`. The relative-link and
anchor check over `docs/content` found no dead path and no missing fragment. The notebook's pictures
are named `quickstart-the-picture.png`, `quickstart-one-mask.png`,
`quickstart-a-palette-mask.png`, `quickstart-a-promptable-segmenter.png`,
`quickstart-a-semantic-segmenter.png`, `quickstart-a-counter.png`,
`quickstart-a-backbone.png`.

## Follow-ups

- **Scrollbars turned accent on hover.** The theme's `pre > code:hover` and
  `.md-sidebar__scrollwrap:hover`/`:focus-within` set
  `scrollbar-color: var(--md-accent-fg-color)`; a reader scrolling a long line or the navigation
  drawer saw the thumb change colour, which looks like a state change in the content.
  `typography.css` pins both to `--md-default-fg-color--lighter` for hover, focus and
  focus-within, and to the same colour for the WebKit thumb, so each scrollbar keeps one appearance.
- **The client download now says how a notebook runs it.** The page shows `curl` because the fence is
  a shell command; `make_notebook.py` prefixes `!`, and the generated `.ipynb`'s first code cell is
  already `!curl -sSO …`. A comment in the fence says so, so a reader who pastes the line into Colab
  adds the `!` instead of getting a Python syntax error. Colab preinstalls NumPy, Pillow and requests;
  the page names the local `pip install` and no `!pip install` cell is needed.

## What to do differently

Use the live demo's measured example when a model has one. It is already a call the service is
expected to answer, it keeps the page and the playground on the same image, and it avoids choosing a
row because its count happened to look good. When a model's output is a field, draw only where the
field has mass; a normalized-by-maximum heatmap is a map of one outlier, not of the answer.
