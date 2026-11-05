# Docs Rules

Package: `docs/`. Shared constraints are in [`project.md`](project.md).

## Sources and publication

- **Retire the site with its consumers, not independently.** It distributes participant clients,
  quickstart assets and the decoder `/play` loads. Replace or retire those dependencies before
  unpublishing and remove dangling DNS records. [Retirement](../../deploy/README.md#64-coordinated-retirement).
- **Publish static content through GitHub Pages at `docs.global-wheat.com`.** Root
  `zensical.toml` builds `docs/content/` into `site/`; the docs workflow publishes main artifacts
  without a participant secret or an API host dependency. Keep environment and build output outside
  the content root to avoid publishing tooling or suppressing assets.
  [Hosting](../references/2026101516_docs-site-hosting.md).
- **Write for participants.** Keep index internals, object layout, upstream provenance and
  development-only dataset reference pages outside the site. Examples may explicitly use the
  development sample for rehearsal, but do not present it as a released dataset. Explain API usage,
  not how this site is built. [Restructure](../references/2026101723_docs-restructure.md).
- **Commit and use `docs/uv.lock`.** Relock dependency changes and build with `--locked` and
  `--strict` so dependency drift or warnings cannot silently change publication.
  [Workflow](../../.github/workflows/docs.yml).
- **Markdown is the notebook's only handwritten source.** Convert `examples/quickstart.md` before
  building. Keep the output-free `quickstart.ipynb` tracked for Colab, but let a validated main
  workflow synchronize that generated file. PRs build regenerated output without committing.
  [Generation](../../docs/scripts/make_notebook.py).
- **Only a trusted successful main run may write back the notebook.** Scope `contents: write` to
  its sync job; stage only the notebook, check the source SHA against main and use a normal push.
  Branch protection still applies, and failed sync blocks deployment. Include `clients/**` in
  workflow paths; client-only edits must republish downloads. Package quality checks remain local,
  not an assumed CI capability. [Workflow](../../.github/workflows/docs.yml),
  [decision](../references/2026102115_documentation-and-implementation-alignment.md).

## Examples and figures

- **Only the recorder writes recorded quickstart outputs.** Never type or patch numbers in
  `<!-- output -->` regions. After code/data/pool changes, run `record_notebook.py` with a key;
  this also writes cell images and removes obsolete ones. A picture is named after its section
  heading, so a restructure renames images: re-record rather than editing a `quickstart-*.png` path by
  hand. CI generation does not execute cells or verify live recorded outputs. The command is in the
  [maintainer README](../../README.md#documentation-site).
  [Recording evidence](../references/2026101811_quickstart-outputs.md).
- **The quickstart has three parts, and each part ends in a result.** Setup, Datasets, Models. Part 3
  executes one model per answer shape — promptable segmentation, semantic segmentation, counting and a
  backbone — and leaves the rest, including a paid generation, in a table. The page stays one
  notebook; splitting it into pages would break the single `.ipynb` source.
  [The pass](../references/2026110602_quickstart-three-parts.md).
- **Keep quickstart Markdown convertible.** Use Python/bash fences, relative images and explicit
  notebook-skip regions; no language tabs or admonitions. Outputs use text-output fences, Markdown
  images or output HTML divs; the converter drops them. Use quiet `curl -sSO` for notebook downloads
  to avoid recording progress output. [Converter](../../docs/scripts/make_notebook.py).
- **Run snippets before publication.** Printed results must come from execution; the static build
  does not supply a token or NumPy. Recorded geometry checks compare decoded mask area to the
  published area. [Recorded examples](../references/2026101722_clients-instead-of-curl.md).
- **Generate figures with the existing client decoders.** `make_figures.py` uses published images
  and recorded geometry. Read the GWFSS palette through `Client().describe`, split masks with
  `global_wheat.label_masks`, and decode RLE through the client; do not hardcode a second semantic
  palette. Regeneration needs `GLOBAL_WHEAT_API_KEY`; normal builds do not. Quickstart cell images
  remain the recorder's output. [Offline regression](../../clients/python/tests/test_figures.py).
- **Generate model parameter tables from a recorded declaration.** `make_models.py` writes marked
  table blocks from `fixtures/model-directory.json`; CI retains the model-page diff check. Refresh
  the fixture from authenticated `GET /models`, never handwrite the schema or require live access
  during a build. [Parameters](../references/2026101811_model-parameter-validation.md).
- **Measure for the participant, not the operator.** A model page's performance section states the
  side the model runs at, the approximate wait, and — where it changes the choice — the quality. Host
  memory, resident process size and allocator peaks belong to deployment records, not the site.
  [Trim record](../references/2026110600_docs-first-run-and-trim.md).
- **A model page follows one order, and names its sources at the end.** At a glance, When to use it,
  Call it (Python / JavaScript / Java / HTTP tabs), Parameters, optional Using it, What comes back,
  optional What you can do with it, Performance, References. References carry Paper (one citation and
  its arXiv/DOI), Code, Weights and License as bullets; the license name is the link. A family adds an
  id table under At a glance rather than a new section.
  [Template record](../references/2026110600_docs-first-run-and-trim.md).
- **A dataset page follows one order, and names its sources at the end.** At a glance, When to use it,
  Get one image (Python / JavaScript / Java / HTTP tabs), What one image holds, optional What you can
  do with it, References. The main Datasets page is the chooser: At a glance, Which one, What one
  image holds, Filters, with no per-dataset references. A sample dataset is a row in the chooser, not
  a released dataset; state its licence or that it declares none. The chooser may say "row" because
  `GET /datasets` counts them; the page itself says image.
  [Dataset trim record](../references/2026110600_docs-first-run-and-trim.md).
- **Call the unit an image; keep `row` as the API's address.** Headings and prose say image. Code,
  JSON fields and paths keep `row`, defined once as the image's position in the pinned revision — not
  a stable id, and not the same thing as the optional `image_id`. A page that says "image id" is
  wrong even when it means "which image".
  [Terminology record](../references/2026110600_docs-first-run-and-trim.md).
- **A hosted model family is one page, one table per id.** Declare the ids in
  `<!-- models: … -->` and carry a `parameters:<id>` block for each; the prose is shared because the
  contract is. The page says the model is served through the provider and links the provider's
  terms; it never describes the private wrapper or its source. Its measured table is filled from
  the host after deployment, not from the upstream's marketing page.
  [Runbook](../../deploy/nanobanana.md).

## Integration and presentation

- **Keep scrollbars neutral.** The theme turns a code block's thumb and the navigation drawer's
  scrollbar accent on hover; `typography.css` pins both to the muted foreground, including focus and
  the WebKit thumb, so scrolling does not read as a change in the content.
  [Fix](../references/2026110602_quickstart-three-parts.md).
- **Frame the API's reference and playground, never duplicate their contract rendering.** The
  gateway owns `/docs`, `/openapi.json` and `/play`; docs must not import the app. One
  `api/frame.js` uses `data-api-frame` to size both frames and forward the reader's theme.
  [Framing](../references/2026101617_the-model-page-and-the-play-page.md).
- **A fine-tune is a new id and a page of its own.** A page groups several ids only when they share
  one provenance and one contract — checkpoints of one release, heads of one release, or tiers of
  one hosted model. A different training set or weight line gets its own `<id>.md`, its own
  `parameters:<id>` table and its own measurements; the models index gets its own row, under
  General or Specialized by what it was trained on. A page that declares several ids carries
  `<!-- models: … -->` and one `parameters:<id>` block per id. A new id refreshes
  `docs/scripts/fixtures/model-directory.json` from `GET /models`, because the parameter table is
  generated from that record. A hand-written model or id count is a second copy of the directory:
  update it in the same change, or replace it with a `GET /models` reference.
  [Models rules](models.md).
- **Handwrite explanatory HTTP policy, not another schema.** `api/http/index.md` explains keys, refusals
  and limits while the API reference remains authoritative for response fields.
  [Participant structure](../references/2026101723_docs-restructure.md).
- **Use the client's decoder as an ES module.** Do not maintain another RLE/palette decoder in
  docs; report failed decoder/example loads visibly rather than showing a misleading blank canvas.
  [Client constraints](clients.md).
- **Copy clients only into build output after a complete build.** `sync_clients.py` publishes the
  five files under `site/clients/`, never under the source tree; GitHub raw MIME types do not serve
  browser modules. Use the [full preview commands](../../README.md#documentation-site) to test
  downloads. [Publication implementation](../../docs/scripts/sync_clients.py).
- **Keep footer credit in the one copyright template override.** Style it in `footer.css`, not a
  second config value; compare the override to upstream when upgrading Zensical.
  [Template](../../docs/overrides/partials/copyright.html).
- **Use English throughout.** Participant content follows the repository's language convention.
  [Conventions](../../README.md#updating-dependencies-and-conventions).
