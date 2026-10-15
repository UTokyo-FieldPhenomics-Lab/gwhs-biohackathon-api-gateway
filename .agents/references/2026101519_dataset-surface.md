# Dataset surface: the index served over HTTP

Plan: [`../plans/2026101519_✅dataset-surface.md`](../plans/2026101519_✅dataset-surface.md). The response
shapes were already written down in the publisher plan's "The JSON the index is built to serve"; this
session implemented them and settled the few things that section left open.

## What was built

- `gateway/src/biohack_gateway/dataset_index.py` — reads the publisher's artifact once at startup:
  the eight tables, the facets, and the shape facts (geometry kind, which metadata fields exist,
  whether an upstream id is carried). Immutable after load, so the read path is thread-safe without a
  lock, and the file is opened `mode=ro`, so the gateway can neither create nor repair it.
- `gateway/src/biohack_gateway/datasets.py` — the Pydantic contract and the four routes.
- `create_app` gained `index_dir` and `data_base_url` and includes the router;
  `gateway/.env.example` records the two names. Both defaults already match the live environment, so
  the host's `env` did not have to change for the routes to work.

## Decisions worth remembering

- **Presence decides a dataset's shape.** A filter key is offered when the index holds a value for it,
  and a row field appears in a response for the same reason. Consequence: a box dataset also offers
  `category` — `gwhd`'s single unnamed class `"0"` — which the publisher plan's illustrative list
  omits. Reading `data-layout.md`'s "the filterable keys are the index fields" literally is what
  produces that, and it is the property that makes a new dataset need no gateway change.
- **One model family, `response_model_exclude_unset=True`.** `mask_url: null` is present because the
  dataset has a mask slot; `classes` is absent because nothing could fill it. A model per dataset
  shape would be three parallel definitions of the same body.
- **The cursor is `base64url({"o": offset})`**, matching the plan's example (`eyJvIjoxMDB9`). It is
  filter-bound by contract, not by enforcement: this repository has no hashing capability, so the
  surface cannot prove a cursor belongs to the filter that produced it.
- **Tags are sorted, and tag order is not promised.** A detail reads them `ORDER BY row, tag`, which
  is the index's canonical read-back order; the plan's example shows the upstream's insertion order
  instead. The two disagree, and JSON array order is not part of the contract.
- **`gwhs-temp` tags include bare numbers** (`"1"`, `"5"`, …), and one category is spelled
  `"spikelet _-_underdeveloped_side1"` with a stray space. Both are upstream data passed through
  verbatim, not parsing artefacts.

## What broke

- **Attributes vanished silently.** `object_attributes` is keyed `(row, idx)`, but the first loader
  grouped it by `row` alone — the same helper that serves `tags` — so every `attributes` lookup by
  `(row, idx)` missed and the detail response had no attributes at all. The fixture tests caught it;
  `_group_by_object` is now separate from `_group_text`.
- **A mypy shadowing error** came from reusing `value` in two loops of `_facets`, the first binding it
  `str` and the second `str | None`. Renamed to `field_value`.
- **Escaping backticks through a template literal three times over** left backslash-backtick sequences in the Python
  docstrings, which is a `SyntaxWarning` and a `D301`. Fixed by writing the backtick literally rather
  than double-escaping it.

## Verification

Local: `ruff format`, `ruff check`, `mypy` (strict) and `pytest` — 103 tests, up from 63.

Real artifact, before deploying: `scp` of the host's `~/.config/biohack-gateway/index/gwhs-temp.db`
and a `TestClient` over it — 10 rows, geometry `rle`, revision
`655118534e9ad980b6f7c0dd6566a406d5a29a27`, row 0 592×2509 with 24 objects and 5 tags, RLE
`[2509, 592]`, and a cursor walk of 3 pages (4/4/2) covering rows 0–9.

Host, after `git pull` and `systemctl restart biohack-gateway` (`f70822a`):

| Check | Result |
|---|---|
| `GET /datasets` without a key | `401` |
| `GET /datasets` | the one real dataset: `geometry rle`, `mask null` |
| `GET /datasets/gwhs-temp` | provenance, 18 tags, 6 categories, 5 attributes |
| `GET …/images?limit=2` | row 0 statistics `{objects: 24, tags: 5}` — the plan's example, measured |
| `GET …/images/0` | `image_id`, tags, and objects with `bbox`/`area`/`attributes`/`rle` |
| `…/images/99`, `?cursor=nope`, `?limit=9999` | `404`, `400`, `400` |
| the `image_url` the API emitted | `200 image/jpeg` from `data.global-wheat.com` |
| `/openapi.json` paths | `/health` and the four dataset paths, nothing else |

The copy of the real index used for the local check lived under `gateway/.cache/` for the length of a
single command and was removed by its `trap`; the index is host state and stays there.

## Next

- Nothing in this item. The plan's next trigger is a second environment, which would want its own
  bucket, distribution, and `BIOHACK_DATA_BASE_URL` — and, per `.agents/rules/project.md`, never a
  prefix inside the live one.
- The `gwfss` and `gwhd` index files do not exist yet, so their description shapes are pinned only by
  the gateway's synthetic fixture. The publisher adapter for each is the item that turns that into a
  measurement.
