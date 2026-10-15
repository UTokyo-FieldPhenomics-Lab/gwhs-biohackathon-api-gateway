# Dataset surface: serve the publisher's index over HTTP

Status: done — implemented and verified on the host on 2026-10-15 — see
[`../references/2026101519_dataset-surface.md`](../references/2026101519_dataset-surface.md)

## Goal and acceptance

Implement the metadata plane's read path in `gateway/`: the four endpoints `2026101518_dataset-publisher.md`
already fixes under "The JSON the index is built to serve". Metadata is loaded from
`~/.config/biohack-gateway/index/<dataset>.db` at startup and answered from memory; a republish is
followed by a restart.

Acceptance: the four routes answer the documented JSON for `gwhs-temp` (and the box and semantic
shapes) on the host, and the gateway's own tests pin every response shape from a synthetic index,
with no dependency on the publisher package.

## What the index gives the surface

Eight tables, frozen in [`../../docs/data-layout.md`](../../docs/data-layout.md): `dataset`,
`classes`, `rows`, `row_classes`, `tags`, `objects`, `object_attributes`, `rles`. The gateway
reads those columns and never writes, augments, or re-derives them.

## Design decisions

1. **Load, don't query per request.** The loader runs the documented SELECTs once at startup and
   builds immutable Python structures. A per-request SQLite connection would make the restart
   contract false and would need a lock; a pure-Python read path is thread-safe by construction.
   Facets are computed once during the same load, so they are a projection and never a second store.
2. **Two modules, by responsibility.** `dataset_index.py` reads the artifact and answers the
   surface's data questions (rows, filters, statistics); `datasets.py` holds the Pydantic response
   models and the router. `app.py` includes the router and stays the auth/error/health module.
3. **Configuration is two host settings**, read by `create_app` with a working default:
   `BIOHACK_INDEX_DIR` (default `~/.config/biohack-gateway/index`) and `BIOHACK_DATA_BASE_URL`
   (default `https://data.global-wheat.com`). Names are recorded in `gateway/.env.example` and in
   `deploy/README.md`; values live in the host's `env`, never in the repository.
4. **Presence decides a dataset's shape.** What a dataset offers is read off the index: a filter key
   is offered when at least one row holds a value for it; `geometry` is `rle` when any object has
   one and `boxes` when objects exist without it; `mask` comes from `dataset.mask_kind`;
   `statistics` carries `objects`, `tags`, and `classes` only where the index has them. This is
   `docs/data-layout.md`'s "the filterable keys are the index fields" read literally.
5. **The cursor is opaque and positional.** It is base64url of `{"o": offset}`, matching the
   publisher plan's example; it is valid only beside the filter that produced it, and the gateway
   neither persists nor validates that pairing. `limit` defaults to 100 and never exceeds 1,000.
6. **One model family, `response_model_exclude_unset=True`.** The contract stays in Pydantic (the
   generated reference has a schema), while a field a dataset does not have is absent exactly as the
   plan's examples show, and a field that is `null` but belongs — `mask_url`, `next_cursor` — is
   present. Three near-identical model families would be the parallel-path failure the rules warn
   against.
7. **No new error paths.** An unknown dataset or row is `404 not_found`; a bad `limit` or cursor is
   `400 invalid_request`; an unknown *filter key* is neither — it simply matches nothing, per
   `data-layout.md`. Client input is never echoed into a message.

## Files

- new `gateway/src/biohack_gateway/dataset_index.py` — load a directory of `<dataset>.db`, build
  facets, and answer row selection, statistics, and detail for one dataset.
- new `gateway/src/biohack_gateway/datasets.py` — Pydantic models and `create_dataset_router`.
- `gateway/src/biohack_gateway/app.py` — `create_app` gains `index_dir` and `data_base_url` and
  includes the router.
- new `gateway/tests/conftest.py`, `test_dataset_index.py`, `test_datasets_surface.py`.
- new `gateway/.env.example`; updates to `.agents/rules/gateway.md`, `deploy/README.md`, the root
  `README.md`, and `docs/data-layout.md` if a detail needs recording.

## Tests

Shape tests per dataset kind plus the edge cases the contract names: unknown filter key is an empty
page; `limit` over 1,000 is `400`; a cursor round-trips and is opaque; an unknown dataset and an
absent row are `404`; every route needs a key; a `null` field that belongs is present and an absent
one is not.

## Verification

`uv run pytest`, `mypy`, `ruff` in `gateway/`; then on the host, with `gwhs-temp.db` in place and
a token issued, the four routes against `https://api.global-wheat.com`.

## Result (2026-10-15)

Local: 103 tests (up from 63), `mypy --strict`, and `ruff` clean in `gateway/`. The real
`gwhs-temp.db` copied off the host loads into the shapes this fixture pins: row 0 answers 592×2509,
24 objects, 5 tags, RLE `[2509, 592]`, and a cursor walk of 3 pages covers rows 0–9.

Host, after the deploy described in `deploy/README.md` (`f70822a`): `/datasets` needs a key and
answers the one real dataset; its description carries 18 tags, 6 categories, and 5 attributes; the
list's row 0 statistics are `{objects: 24, tags: 5}`; the detail returns tags and geometry; an absent
row, a bad cursor, and a `limit` over 1,000 answer `404`, `400`, `400`; and the `image_url` the API
emitted resolves to `200 image/jpeg`. OpenAPI lists exactly `/health` and the four dataset paths.
