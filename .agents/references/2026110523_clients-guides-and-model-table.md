# One client guide per language, and a model table that is copyable

The clients page was one long page with tabbed load snippets and a shared method table. It worked as
a reference but not as a per-language guide: a Python reader had to skip JavaScript and Java, and a
Node reader met the `sharp` dependency halfway down. The quickstart also stopped after one model.
This pass split the guides, added a copyable call for every id, and made the nav express the two
model families the index already describes.

## The client guides

`docs/content/api/clients/index.md` is now the overview: the language table, the shared key, the
full method table, calling conventions, decoding and errors. Three pages carry the rest:

- `python.md` — one file, a runnable script, NumPy/Pillow containers, the parameter table.
- `javascript.md` — browser and Node entries, the CORS limit, the Node script, typed arrays.
- `java.md` — one file, `javac`, the runnable `Main`, `Mask`/`Features`, `Map` parameters.

Each has the same runnable spine: `client.image("gwhd", 0)`, then
`client.predict("sam3.1-meta", "gwhs-temp", 0, prompt="wheat head")`. The Python, Node and Java
versions were run against the live API before this record was written. The browser example is a load
snippet, not a live call, because the API sends no CORS headers; a page on another origin cannot call
it.

**The Node example found a real gap.** The two `.js` files are ES modules, but Node reads a `.js` file
as CommonJS unless a `package.json` with `"type": "module"` sits above it. The first run failed with
`Named export 'Client' not found`. The JS guide now says to run
`npm init -y && npm pkg set type=module` beside the two files, and the overview's table says the Node
entry needs `sharp` and an ES-module `package.json`. A `.mjs` entry alone is not enough; the imported
`.js` files follow the package boundary, not the entry's extension.

The overview no longer repeats the load commands. Its language table links to the three guides; the
shared method table stays in one place so a method name cannot drift across three copies.

## The quickstart table

`docs/content/examples/quickstart.md` gained `## Model calls at a glance`, after the failure step and
before `Next`. It has one line per model id — grouped only for the three Nano Banana tiers — with the
answer shape beside it. It is deliberately not executed: the Nano Banana call spends image credits,
and `wevisone-vl-4b` is a long call. The table is a copy-paste reference, not another notebook step;
the notebook intro still says ten steps.

The table also fixes a wording error in the model index: `generate` is how the Nano Banana ids are
called, not a route every image model has. `internvl-u` produces its picture through `predict` with
`mode`.

## The nav

`Calling the API` now holds a `Clients` section with `Python`, `JavaScript` and `Java` underneath.
`Models` holds `General models` and `Specialized models`, matching the headings on the model index.
Both are nested lists in `zensical.toml`; Zensical's converter walks them recursively, and
`navigation.indexes` makes the section's `index.md` the clickable parent. `make_models.py` walks the
same nav and would have failed on a model page that no reader could reach.

## Rules

- `project.md` now says a new model id or API route is one change across gateway, clients and docs;
  a new route updates `routes.json` and the contract test, a new answer field needs its decoder in
  all three clients, and a docs-only model page is a promise the API cannot keep.
- `clients.md` says a model id alone needs no new method, but a route or answer field does.
- `docs.md` says a fine-tune is a new id and a page of its own; a page groups only checkpoints with
  the same weights and provenance. It also treats a hand-written model count as a second copy of the
  directory.
- `models.md` extends the two-checkpoints rule: a fine-tune is another id, never a flag on the
  upstream one.

## Verification

```bash
python3 docs/scripts/make_models.py
python3 docs/scripts/make_notebook.py
uv run --project docs zensical build --strict
```

The model pages were already current; the build reported no issues; the relative-link check found
nothing. The Python, Node and Java examples printed a 1024 × 1024 row with 144 objects and one
`sam3.1-meta` object at 1008 px.

## What is left

- **The hand-written counts.** The models index and the front page still say “nine models, thirteen
  ids”. The new docs rule makes the next model author update them, but a `GET /models` reference or
  a generated count would remove the drift entirely.
- **Nano Banana 2K and Pro remain unmeasured.** The quickstart table names the calls, not their
  numbers.
- **The Node example needs `sharp` installed.** That is the client's one dependency and the page
  says so; a participant without a compiler may prefer the browser entry.
