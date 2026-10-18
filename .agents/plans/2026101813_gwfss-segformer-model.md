# `gwfss-segformer-b4` — the dataset's own baseline, served

## Goal

One more model id, `gwfss-segformer-b4`: the segmentation baseline published with the dataset we
already serve, answering `gwfss` rows and uploads through the same two routes as SAM. It takes **no
parameters**, returns one object per organ class, and lands on `labpc` first, then on all three
machines, then on `/play`.

The reason it is worth serving at all is that it closes a loop nothing else on the platform closes:
`gwfss` is `GlobalWheat/GWFSS_v1.0`, these weights were trained by the people who made it, and the
answer is directly comparable with the mask the dataset publishes for the same row. A participant
gets a model answer and its ground truth from the same two calls.

**Scope: B4 only.** The B1 checkpoint (`GlobalWheat/GWFSS_Segformer_b1`, renamed from `_b0` on
2026-10-18, same tree, sha `485865aa`) is documented and linked, not served — see D2.

## What was measured before this plan

On the laboratory 3090, fp32, torch 2.13 + transformers 4.55.2, the paper's own recipe (bilinear
upsample of the 1/4-resolution logits, then argmax), on the ten `gwfss` rows that are published
today:

| | B1 (not served) | **B4 (this plan)** |
|---|---|---|
| parameters | 13.68 M | **64.00 M** |
| safetensors | 54.7 MB | **256.1 MB** |
| one 512² inference | 6.6 ms | **24.8 ms** |
| 16 CPU threads | 243 ms | **857 ms** |
| GPU added, loaded → one inference | 322 → 551 MiB | **655 → 1115 MiB** |
| mIoU, 4 classes | 0.665 | **0.701** |
| per class: background / head / stem / leaf | .744 / .857 / .237 / .823 | **.747 / .866 / .366 / .825** |
| pixel accuracy | 0.881 | **0.884** |

These are the reference numbers for P1 and P2, not targets: a container that answers much slower, or
asks for much more memory, has something wrong with it.

## Decisions

**D1 — One image, one id, no variant parameter.** A `variant=b1|b4` query parameter would let one
request choose a different weight set, and therefore a different memory figure, which is the one
thing a parameter may not do (`rules/gateway.md`, the `imgsz` ceiling). The declaration is also one
per container: two ids behind one `/health` means one of them is permanently "declared as the other".
The Dockerfile still selects its checkpoint from `GWFSS_WEIGHTS`, so a second checkpoint later is a
second container, not a second codebase.

**D2 — B4, not B1.** The API removes the constraint that makes a small backbone attractive: 24.8 ms
is already 40 calls per second per card, and both checkpoints fit beside SAM without thinking. B4 is
better on every class and much better on stems. The model page says in one sentence that the paper's
Table 4 used B1 and links the public weights, so the choice is visible rather than hidden.

**D3 — Semantic output becomes one object per class.** `background, head, stem, leaf` are argmaxed
per pixel; background is not returned (it is the complement). Each object carries `category` = the
class name, `bbox` = that class's extent, `area` = its pixel count, `rle` = COCO compressed geometry
and `score` = the mean probability of the winning class over that class's pixels. The score is
documented as derived and uncalibrated, not as a detector confidence. No connected-component pass:
for heads it would be useful and for leaves it would return thousands of fragments, and it is a
post-process we would be inventing. A five-line numpy example on the page is where counting belongs.

**D4 — No parameters at all.** The model takes an image and nothing else. The declaration publishes
`parameters: []` with `declared: true`, so every query parameter is refused with the model's own
(empty) surface named in the message. This is the first real model that exercises
"declares nothing" as distinct from "has not declared yet".

**D5 — Geometry stays RLE; no PNG, no object storage.** A 512² answer with three classes measured
**25.6 kB** of JSON (17.1 kB gzipped; the same class map re-encoded as a 4-colour PNG is 13.2 kB, and
base64 would put it back at ~17.6 kB). PNG would buy nothing and would introduce a second geometry
encoding beside the one the decoder, the three clients and the dataset surface already share.

**D6 — The checkpoint is fetched at build, pinned by revision.** `model.safetensors` is
256,103,280 bytes at `6349b1a281d7d305845cac8ad56d2ae0d0060794`, sha256
`af121d2f9b878b043df5cd13e43391319ca3878ac225f13dc042bbbff0edbca4`, CC-BY-4.0. The build asserts
the byte count, as the SAM build does, so a truncated or replaced file fails at build rather than at
inference.

**D7 — Ten rows for now.** The published `gwfss` set is ten rows (`gwfss.db` says 10, as do the ten
objects in the bucket); the full 1,096 are published **after** the whole path has been rehearsed and
before the event. Nothing in this plan publishes dataset objects, and the model page says "ten rows
today" where it could otherwise be read as the whole set.

**D8 — Deploy order and the port.** Container first, then the gateway: a declaration is read once
and never re-read. On `labpc` the container publishes `12713` on all interfaces (`-p 12713:12713`) —
binding it to `127.0.0.1` makes it unreachable from the gateway host, which is not a failure the
container can see.

## Steps

### P0 — Rules and the open door

- `rules/models.md` gains three lines: a checkpoint is an id and never a parameter; a semantic model
  answers one object per class with background omitted, and its box is an extent, not an instance;
  model geometry is RLE, and no second encoding is introduced for a model that would find one
  convenient.
- `rules/gateway.md` needs no change — the existing parameter rule already covers D1 and D4.

**Acceptance:** the three lines read as constraints, not as history, and the parameter rule they lean
on is still the one quoted.

### P1 — The container

`models/gwfss_segformer/`, laid out like `sam31_official/` and much smaller:

| part | owns |
|---|---|
| `Dockerfile` | CUDA runtime + `uv` + torch + `transformers==4.55.2`; weights fetched from HF at the pinned revision with a byte-count check; labels `io.biohack.model`, `io.biohack.input_sizes="512"` and the two VRAM figures |
| `serve.py` | `/health` declaration (`model`, `resolution: 512`, `parameters: []`, `max_in_flight`, `computed_by`), `POST /models/{model}/predict`, `…/predict/upload`, token check, the `data.global-wheat.com` fetch guard the SAM container already has |
| `outputs.py` | logits → per-class masks → the object entries, reusing the same COCO RLE encoder |
| `tests/labels.py` | image labels against the process's own defaults, as `sam31_official/tests/labels.py` does |
| `tests/outputs.py` | the RLE this container emits decodes, with the published decoder, to the mask it encoded |
| `tests/bench.py` | the ten-row measurement above: per-class IoU, median latency, VRAM delta |

**Acceptance:** all three tests pass on `labpc` with no GPU argument for `labels.py`/`outputs.py`; an
answer for row 4 has three objects, `Σ area` = 262,144 − background pixels, and every `rle` decodes
to its own `area`; a request carrying any query parameter is refused; `/health` answers without a
token and names the model.

### P2 — `labpc`, and the first real call

- Build on `labpc`, run with `--gpus all -p 12713:12713 -e BACKEND_TOKEN=… -e GWFSS_MACHINE=lab-3090`,
  no `restart: always` (the admission refusal must stay visible).
- Measure `resident_gb` and `per_request_gb` the way the SAM figures were measured (card usage before
  the container, after loading, after one inference) and put the numbers in the labels.
- Add `"gwfss-segformer-b4": ["http://<address>:12713"]` to the host's `models.json`, then
  restart the gateway.
- Call it through the gateway, not around it.

**Acceptance:** `GET /models` lists the id with `declared: true`, `parameters: []`, `resolution: 512`,
`backends_up: 1`; `POST /models/gwfss-segformer-b4/predict` with `{"dataset":"gwfss","row":4}` returns
three objects; `?prompt=x` is a `400` whose message lists what the model does accept (nothing);
`predict/upload` with a photograph returns an answer; the gateway's answer and a direct call agree
object for object.

### P3 — Documentation

- Record `GET /models` into `docs/scripts/fixtures/model-directory.json` and regenerate the table.
- Fix `make_models.py`'s empty branch: it currently prints "No backend has declared this model yet"
  for any model with no parameters, which is the wrong sentence for one that declares none. Use
  `declared` to choose between the two.
- New page `docs/content/models/gwfss-segformer-b4.md`: the four classes and what each means in the
  dataset's own vocabulary, 512 px, no parameters, **semantic not instance** (the head mask is area,
  not a count), stems are the weak class with the measured numbers, B1 is the paper's Table 4 model
  and is linked rather than served, CC-BY-4.0, and "ten rows are published today".
- A figure: row 4's prediction beside its published mask. The prediction is recorded once into
  `docs/scripts/fixtures/gwfss-row4-prediction.json` and drawn by `make_figures.py` through the
  published decoder, so the site build stays offline and deterministic.
- `docs/content/models/index.md`: "One model answers today" becomes two, and the table gains a row.
- Keep the paper's numbers attributed to the paper: ~90 % leaves and heads, 45–54 % stems, on its
  own splits — never restated as this checkpoint's performance on our rows.

**Acceptance:** the workflow's `git diff --exit-code` is clean after regenerating; the page's table is
generated, not typed; the sentence about the ten rows is present; no page claims 1,096 rows are live.

### P4 — Three machines

- The same image on `homepc-4090` and `lab-5090`, machine names set, `models.json` listing all three
  addresses, gateway restarted **after** the last container.
- One call per backend and one call through the pool; `computed_by.machine` differs per backend.

**Acceptance:** `backends_up: 3` for this id; each machine answers; the three classes are present
everywhere and their areas agree within a tolerance recorded with the result. They are **not**
promised identical: this pool has already seen one call return 18 objects on the 3090 and 17 on the
5090, so a card difference is a fact to record rather than a defect to chase.

### P5 — `/play`

The page is declaration-driven, so the model appears as soon as P2's restart lands; this step is what
makes it a demo rather than a coincidence.

- Verify: the model dropdown lists it; selecting it shows **no** parameter controls and no resolution
  restatement; the request line carries no query string; run on `gwfss` row 4 paints the three
  classes over the image; upload works; the dataset picker offers `gwfss` (ten rows).
- The model page links `/play` with the model preselected, so "try it" is one click.

**Acceptance:** a browser run from the live gateway, on the published page, does all of the above;
the screenshot or recorded answer is committed beside the page if the existing style calls for one.

## What is not in this plan

- **B1 as a second id.** The door is D1's env-selected checkpoint; opening it is a container, not a
  code change.
- **Connected components / head counting.** Documented as a snippet; not a parameter, not an output
  mode.
- **Publishing the full 1,096 `gwfss` rows.** A separate step, deliberately after this path is
  rehearsed, as agreed on 2026-10-18.
- **YOLO or any other model.** The per-model surface is already generic; this plan does not test it
  further.

## Risks, and what the page must therefore say

| risk | what it costs | what the page does about it |
|---|---|---|
| the checkpoint's training split is undocumented | accuracy on published rows is not a generalisation result | says the release documents no split, and quotes the paper's numbers as the paper's |
| background↔leaf is the largest error, not stems | a participant may read mIoU 0.70 as "leaves are fine everywhere" | shows the confusion in words — straw and soil take leaf pixels, and row 4 is a harvested sample |
| stems are far below the paper's figure on our rows (0.366 vs 45–54 %) | the headline number would be misleading if copied | prints the measured per-class numbers on this page |
| RLE at upload resolution grows with the boundary | a 12 MP upload can answer a few hundred kB | documented; no cap, same as SAM |
| a parameterless model looks broken in a UI built for parameters | a blank control area reads as a bug | checked in P5; the legend is hidden, and the page says the model takes no parameters |
