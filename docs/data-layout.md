# Data Layout

Handwritten contract for the static data plane. Together with the Pydantic models in
`gateway/`, this is one of only two handwritten contract sources in the repository.

Status: **pending**. Content is added once the upstream schema question is resolved — the
current sample dataset exposes `image`, `image_id`, `width`, `height`, `tags`,
`tags_onehot`, and `objects`, but not the `site` and `stage` fields the layout depends on.
See the architecture note, sections 4 and 13.

To be defined here:

- S3 layout: `index.jsonl`, per-prefix index files, `manifest.json`, and the object paths.
- Index fields: which attributes live in the index (multi-valued or mutable).
- Path levels: which attributes are encoded into the path (single-valued and immutable).
- Manifest fields: version, counts, institution and stage enumerations, license, release date.
