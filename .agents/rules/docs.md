# Docs Rules

Package: `docs/`. Repo-wide invariants are in [`project.md`](project.md) and are not repeated here.

- **Disposable by design.** The site exists for the event window and may be deleted whole afterwards,
  so no production pipeline may depend on it. Retiring it means removing the `docs` DNS record as
  well, or the name stays claimable by someone else.
- **Hosted by GitHub Pages at `docs.global-wheat.com`.** `zensical.toml` at the repository root builds
  `docs/content/` into `site/`, and `.github/workflows/docs.yml` publishes that artifact from `main`
  with `--strict`; nothing in the build holds a secret, and no host of ours is involved. The hand
  steps behind the domain are `deploy/README.md` §6.
- **The content root holds site material only.** `docs_dir` is `docs/content/`, never `docs/`, because
  a virtualenv anywhere under the content root stops the build emitting `site/assets/` entirely: the
  pages publish unstyled and the build reports no problem. The subproject's `pyproject.toml`,
  `uv.lock`, and `.venv` therefore sit outside it.
- **Every published page is written for a participant.** The site answers what the API offers, how to
  call it, what comes back, and what the examples are. Internal material — index fields and tables,
  the object-key layout, upstream provenance, and the development sample — stays outside
  `docs/content/`, which is why `data-layout.md` sits beside the subproject rather than inside it.
- **Locked like the rest.** `docs/` commits a `uv.lock` like every other subproject:
  `requires-python` is a floor and the lock pins the rest. Re-run `uv lock` whenever `pyproject.toml`
  changes.
- **Markdown is the source of truth.** No notebook is committed, and no output is committed with a
  snippet.
- **The reference is the API's own page, framed.** Zensical has no OpenAPI plugin and needs none:
  FastAPI serves `/docs` and `/openapi.json` at runtime, and `docs/content/interactive.md` frames that
  page so a reader stays inside the site. There is no generated markdown copy of the reference, in
  `docs/content/api/` or anywhere else — a second rendering of the same contract would be a second
  thing to keep true.
- **The gateway owns the served reference, `docs/` never imports the app.** The page the site frames
  is registered by the application, which is what lets it carry the frame's height reporter and the
  dark palette; see [`gateway.md`](gateway.md). The site itself has no Python dependency on the
  gateway and builds from markdown alone.
- **Policy prose is handwritten, and there is none yet.** Error, auth, and rate-limit policy has no
  page; when it earns one it is written by hand, because the endpoint reference is the runtime OpenAPI
  document rather than a page kept in step with it.
- **A snippet ships only after it has been run.** The site's snippets were executed against real rows
  before publication — the palette split in three languages, the RLE decoder against an object whose
  `area` it has to equal — and the numbers they print are the printed numbers. The build does not
  execute them: it has no NumPy and no token, so a page that needs either is verified by hand.
- **Figures are generated, not drawn.** `docs/scripts/make_figures.py` builds every image the pages
  show from published rows and one recorded geometry fixture, so a figure can be regenerated when the
  data changes. It lives outside `docs/content/`, and it needs `--with pillow --with numpy` because
  the site's own environment carries neither.
- **The frame's two files live in `docs/content/api/`.** That directory holds no page — the reference
  is a frame inside `interactive.md`, and the run-a-model page is a frame inside `models/index.md` — so
  it holds the stylesheet that hides the heading of a page that is nothing but a frame, and the script
  that sizes every frame marked `data-api-frame` and passes the reader's palette into it. A frame is
  matched by that attribute rather than by the id of the box it happens to sit in, so one file sizes
  every frame the site shows and a new one needs no change here.
- **One decoder file, and it is the site's own.** `post-processing/rle_decode.js` is what the
  Post-processing page tells a participant to download and what the Models page's demo loads by URL;
  the API's own run-a-model page loads that same file from this site rather than carrying a copy, so a
  demo and a tutorial cannot disagree about what a correct decode is. Anything on a page that can fail
  to load — a decoder, a recorded example — says so in words where the reader is looking, because a
  blank canvas reads as a result.
- **English only**, consistent with the rest of the repository.
