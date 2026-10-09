# Docs Rules

Package: `docs/`. Repo-wide invariants are in [`project.md`](project.md) and are not
repeated here.

- **Disposable by design.** The site exists for the event window and may be deleted whole
  afterwards, so no production pipeline may depend on it.
- **Locked like the rest.** `docs/` commits a `uv.lock` like every other subproject:
  `requires-python` is a floor and the lock pins the rest. Re-run `uv lock` whenever
  `pyproject.toml` changes.
- **Markdown is the source of truth.** `.ipynb` is derived in CI by a deterministic tool
  (jupytext), never hand-edited and never committed with outputs.
- **API reference needs no plugin.** Zensical has no OpenAPI plugin; use FastAPI's runtime
  `/docs` and `/openapi.json` plus generated markdown instead of a third-party renderer.
- **Generation belongs to the gateway environment.** `docs/` never imports the app; a
  generator in the gateway environment imports `biohack_gateway`, writes the committed
  markdown, and `docs/` only renders it.
- **Policy prose is handwritten, references are generated.** Error, auth, and rate-limit
  policy will live in `docs/api/errors.md`; endpoints, fields, and examples come from the app.
- **Examples run in CI.** Snippets and notebooks execute during the build; pages needing a
  GPU are excluded and run once by hand before release.
- **English only**, consistent with the rest of the repository.
