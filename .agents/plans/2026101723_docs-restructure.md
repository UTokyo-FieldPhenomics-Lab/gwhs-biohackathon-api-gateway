# Restructure the docs site around what a participant asks

## Steps

- [x] `docs/scripts/make_notebook.py`: markdown → `.ipynb`, and the page's conversion constraints.
- [x] `api/`: `http.md`, `clients.md`, `reference.md` — the contract, the wrappers, the full list.
- [x] `datasets/`: a summary page and one page per dataset; mask figures move here.
- [x] `models/`: a summary page carrying the pool, and one page per model.
- [x] `examples/`: the live demo page, and the notebook page the `.ipynb` is generated from.
- [x] `index.md`: orientation only — three addresses, what you can get, one call, where to go.
- [x] Nav, `make_figures.py` output paths, the workflow, and the three rules that change.

## Why

The site grew as five pages that each retold the same three things: which routes exist, how to call
them, and what a mask is. A participant opening any page had to work out which of the three they were
looking at. The restructure gives each page one question to answer, and puts every answer where the
question is first asked.

- **Requests are taught once**, in `api/http.md` (the contract) and `api/clients.md` (the wrappers).
  A capability page shows the response and one line that produces it; anything with a second step —
  pagination, fetching bytes, decoding, drawing — belongs to `examples/`.
- **One section per item.** A dataset is a page, a model is a page: the left nav is the index, and a
  new dataset or model is a new file rather than a new heading inside a growing one.
- **The notebook is generated, never hand-written.** `examples/notebook.md` is the source; the
  `.ipynb` beside it is build output that is also committed, because Colab's only input is a GitHub
  path. `git diff --exit-code` after the build is what keeps the two from drifting.
- **A page explains the API, not the site's own design.** Sentences about why the site is built this
  way are `.agents/` material; that was most of the verbosity.

## Page budgets

| page | lines |
|---|---|
| `index.md` | 40 |
| `api/http.md` | 65 |
| `api/clients.md` | 140 |
| `api/reference.md` | 10 |
| `datasets/index.md` | 50 |
| `datasets/gwhd.md` | 55 |
| `datasets/gwfss.md` | 50 |
| `datasets/gwhs.md` | 60 |
| `models/index.md` | 70 |
| `models/sam3.1-meta.md` | 80 |
| `examples/demo.md` | 60 |
| `examples/notebook.md` | 120 + `.ipynb` |

## Constraints that change

- `docs.md`: "No notebook is committed" becomes "the notebook page is the source and the `.ipynb` is
  its generated, committed output"; the conversion constraints become a rule; the frames rule points
  at the reference and the demo page instead of the reference and the models page.
- `backend-pool.md`: `homepc-4090` now answers — the row said the gateway does not route to it.

## Verification

`uv run --project docs zensical build --strict` (broken links fail), `make_notebook.py` regenerating
an unchanged file (idempotent), `git diff --exit-code` on the `.ipynb`, and one hand run of the
notebook page's code against the live API.
