# A participant docs pass: first run first, then trim

The site had grown accurate but manual-shaped: a page often explained the mechanism before the reader
had a result, and several "copy this" examples assumed a variable, a package or an HTTP method the
page never gave. This pass reordered the entry points, made the first-run path complete in each
language, corrected the contract statements that disagreed with the implementation, and removed the
parts of the prose that only restated the design.

## What was wrong, and what changed

- **A generate example was a GET.** `curl` without `-d` or `-X POST` sends GET, so both Nano Banana
  examples could not have worked. They now pass `-X POST`; the HTTP page gained the `API`/`KEY`
  setup block the rest of its calls assumed.
- **Raw HTTP taught a different status than the gateway returns.** A missing row is `404 not_found`
  (`no such row`), not `400`, and `429` can come from a backend's capacity or a team allowance. The
  refusal table, the last paragraph and the skill now say which refusal retrying can clear and which
  cannot. A retried `generate` is a new charge.
- **"Nothing to install" was false for two clients.** Python needs `numpy`, `requests` and `pillow`;
  Node needs `sharp`, an ES-module `package.json` and a run command. Both pages now carry a complete
  setup block, and the Java page states JDK 17 rather than "the JDK you already have". The Android
  claim is gone: the source imports `java.awt` and `javax.imageio`, which Android does not provide.
- **A picture was described as the encoded file only.** The clients crop to `frame` on an edit at
  `auto`; all three language pages, the clients overview and the HTTP prose/picture section now say
  that, and keep `image.data` as the model's file.
- **The client overview carried the model tutorials.** Counting and backbone recipes compressed into
  one "Decoded answers" table; the detail now lives on the model or HTTP page it belongs to. The page
  is roughly 40 lines shorter.
- **The entry points did not agree on where to start.** The home page now lists four starting points
  (demo, notebook, client, agent) before the addresses, then a key and a first call. "Calling the
  API" is a chooser table rather than a recommendation to read the whole HTTP page first.
- **The models index answered "what is it" before "which one".** A task table precedes the catalogue;
  the catalogue keeps provenance and hardware. The "datasets and weights are public" claim now
  excludes the hosted model.
- **Raw HTTP carried a Pillow recipe.** The page is the wire contract, so the Python upload-resize
  block is gone; it now states the multipart field, the model's own `resolution`, the 15 MB / 50 MP
  limits and the client switch, and links the client that does the work.
- **Model pages published operator memory.** Resident and allocator figures were removed from
  TasselNetV4, InternVL-U, YOLO26 and DINOv3; what remains is the side the model runs at, the wait,
  and — for the backbones — the answer size that follows from the side.
- **Two model pages said the opposite of their own body.** WeVisOne's "Fast" is now "Slow", and
  "One phrase per call" is "Single-class". TasselNet's "no box parameter" now distinguishes an
  exemplar from a region of interest. SPROUT returns three foreground classes, not four objects.
- **Model pages each told their own story.** They now share one order: At a glance, When to use it,
  Call it (Python / JavaScript / Java / HTTP tabs), Parameters, optional Using it, What comes back,
  optional What you can do with it, Performance, References. A family page puts its id table under
  At a glance. The HTTP tab carries the same minimal call as a curl command, with `API` and `KEY` as
  the Raw HTTP page defines them.
- **Dataset pages had no shared order.** They now follow At a glance, When to use it, Get one image
  (Python / JavaScript / Java / HTTP), What one image holds, optional What you can do with it,
  References. The main Datasets page is the chooser. `gwhs-temp` is a row there with its own status,
  and its page no longer claims CC-BY-4.0: the sample declares no licence. GWHD follows the pinned
  dataset card's CC-BY-4.0, not the paper's MIT FAQ.
- **`row` was doing two jobs.** The site used it for the unit (an image) and for the API's position
  field. Headings, tables and prose now say image; `row` stays in code, JSON and paths, defined once as
  the image's position in the pinned revision, not a stable id. It is not renamed to "image id":
  `image_id` is a separate upstream field, and row numbers repeat across datasets and can be
  renumbered by a new revision.
- **A section and its subsection looked alike.** The theme set H2 at 22.5 px and H3 at 18.75 px, both
  weight 700 and body colour. `typography.css` now gives H2 a larger size and a thin rule above it, and
  H3 a smaller size, weight 600 and a muted colour. No numbering: optional sections would make manual
  numbers drift, and CSS counters would not appear in the generated TOC. Tables carry column titles
  (`field | value`), and a nested bullet list gets one indent step instead of sitting under its parent.
- **References were scattered.** Every page ends with Paper / Code / Weights / License bullets; the
  license name is the link, and a hosted model lists provider terms with no weights. The paper is one
  citation line plus its arXiv or DOI link — no BibTeX block.
- **The dataset envelope mixed a list and a detail.** `docs/content/datasets/index.md` now shows both
  shapes; the detail example no longer carries a `statistics` field the response does not have. Each
  dataset page gets one row before its field tour.
- **The agent page repeated itself.** Four sections with overlapping lists became four short ones:
  the key, the sentence, the live contract, the skills directory. The long/short prompt duplication
  is gone.
- **A wide table lost its last column on a phone.** The theme makes a table `inline-block`, so cells
  overflowed a wrapper that could not scroll. `typography.css` keeps the table a table and lets its
  wrapper scroll; the clients table and the parameter tables keep every column reachable.

## Verification

```
python3 docs/scripts/make_models.py
python3 docs/scripts/make_notebook.py
uv run --project docs zensical build --strict     # No issues found
git diff --check                                  # clean
```

A relative-link check over `docs/content`, `.agents/rules`, `AGENTS.md`, `README.md`,
`docs/data-layout.md` and the skill found no dead path. An anchor check over every `page.md#fragment`
found none missing. Every model page was checked for the seven required H2 headings in order. The home
page was viewed at 1280×900 and 390×844; the starting-point table wraps without a horizontal scroll
and the sidebar still expands every group.

## What was deliberately not done

- **The quickstart's step order was left for a follow-up.** Moving the model call ahead of the
  annotation tour would move recorded cells and images, so it needed `record_notebook.py` with a live
  key in the same change. It was replaced by three sections in
  [the quickstart pass](2026110602_quickstart-three-parts.md).
- **Performance sections share a heading, not a metric.** Quality is IoU or MAE where the model has
  one; Speed is latency. A model without a quality number carries only Speed, rather than an empty
  section.
- **No live calls were made.** Every change is checkable against the code and the recorded fixtures;
  the next re-record is the moment to confirm the examples against the running pool.

## What to do differently

A participant page is not a design note. If a block says "copy this", it must carry the variables it
uses, the dependency it needs and the method it sends; if a status appears in prose and in a table,
both must be read from the same implementation. Trim by moving detail to the page that owns it, not by
deleting the sentence that makes the remaining example true.
