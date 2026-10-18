# The quickstart's recorded outputs

Date: 2026-10-18. The page `docs/content/examples/quickstart.md` was a notebook with no answers: a
reader saw ten cells and had to run them to learn what comes back. It now shows one recorded result
under each cell that produces one, and the `.ipynb` beside it still ships empty.

## What was built

| piece | change |
|---|---|
| `docs/scripts/make_notebook.py` | drops `<!-- output -->` … `<!-- /output -->` regions as well as `notebook: skip` regions (`strip_skipped`), and its docstring says where they come from |
| `docs/scripts/record_notebook.py` | new: executes the notebook the page generates, rewrites the page's regions, writes the displayed pictures beside the page |
| `docs/content/output.css` | new: a result takes a code block's monospace without its card, with a small `Output` label, aligned with the code above |
| `zensical.toml` | `extra_css = ["footer.css", "output.css"]` |
| `quickstart.md` | `curl -sSO`, and eleven recorded regions |
| `quickstart-step5.png`, `-step6.png` | what the two image cells display, committed |
| `.agents/rules/docs.md` | "no output is committed with a snippet" became "the notebook commits no output; the page's are recorded" |

The notebook is unchanged in kind: 26 cells, 13 of them code, `outputs: []` throughout, no marker
text in any cell source. The only `.ipynb` diff from before this work is the `curl -sSO` line.

## How it records

```
set -a; . ./.env; set +a
uv run --project docs --with nbconvert --with ipykernel --with requests --with numpy \
    --with pillow python docs/scripts/record_notebook.py
```

1. `make_notebook`'s own functions build the notebook from the page, in memory.
2. It is written to a temporary directory and executed there with `nbconvert --execute`. The page's
   `curl` fetches `global_wheat.py` from `docs.global-wheat.com` into that directory, so the snippet a
   participant is told to run is the one that ran here.
3. `stdout` and every result are collected per cell — `text/plain`, `image/png`, or `text/html` —
   with stderr dropped.
4. The page's regions are deleted, then re-emitted in fence order; pictures become
   `quickstart-step<N>.png`, `<N>` taken from the enclosing `## N.` heading; any
   `quickstart-step*.png` not written this run is removed.

A second run immediately after the first produced a byte-identical page, so the rewrite is stable.

## Decisions, and what would have been cheaper but worse

- **Not markdown-exec.** `docs/` carries it as a dependency and never wires it into
  `zensical.toml`. Executing at build time was refused: the build has no token and no NumPy, a
  GitHub build must never call the live API, and `--strict` publishing would then depend on a GPU
  being up.
- **Nothing typed by hand.** A transcript pasted into the page drifts silently the moment a cell
  changes; here the numbers exist because a kernel printed them.
- **PNG files, not base64.** Inline base64 would bloat the HTML, defeat caching, and turn a one-line
  page edit into an unreviewable diff.
- **No `In [n]:` / `Out [n]:` gutter.** Jupyter's counters are execution order — the key cell is
  skipped, a re-run renumbers — so a hardcoded number on the page would be a promise the page cannot
  keep. The look is kept: monospace, no card, no clipboard button, hugging the cell above.
- **No structural check in the converter.** An earlier plan had `make_notebook.py` refuse a python
  fence with no recorded region; it was dropped as unnecessary coupling. The recorder is the only
  writer, so a missing region means a cell that printed nothing — as step 2 does.

## Observed

- `curl`'s progress meter arrived as **stdout**, not stderr, so the page's first cell was changed to
  `curl -sSO`: a progress bar is not a result and would have been recorded.
- Recorded outputs: three datasets, four filter/category names, `592 x 2509 — 24 objects`, mask
  `(2509, 592) bool 12074 12074.0`, `sam3.1-meta at 1008 px` from `lab-5090`, `404 not_found`, and
  the two pictures at 283×1200.
- The two PNGs are ~500 KB each: they are photos at display size, and JPEG would be a quarter of
  that, but a picture a cell returned is kept in the format the kernel produced.

## What is still not checked by CI

Nothing validates that these numbers are still true: the workflow has no token, so it builds what was
committed. Re-run the recorder whenever the page's cells change, when a dataset is republished, or
when the model pool moves — a prediction's area and score are one machine's, so a re-record may
legitimately differ in step 7 while every other step stays identical.
