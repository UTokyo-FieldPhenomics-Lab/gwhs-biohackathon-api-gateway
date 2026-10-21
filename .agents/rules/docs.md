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
  this also writes cell images and removes obsolete ones. CI generation does not execute cells or
  verify live recorded outputs. The command is in the [maintainer README](../../README.md#documentation-site).
  [Recording evidence](../references/2026101811_quickstart-outputs.md).
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

## Integration and presentation

- **Frame the API's reference and playground, never duplicate their contract rendering.** The
  gateway owns `/docs`, `/openapi.json` and `/play`; docs must not import the app. One
  `api/frame.js` uses `data-api-frame` to size both frames and forward the reader's theme.
  [Framing](../references/2026101617_the-model-page-and-the-play-page.md).
- **Handwrite explanatory HTTP policy, not another schema.** `api/http.md` explains keys, refusals
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
