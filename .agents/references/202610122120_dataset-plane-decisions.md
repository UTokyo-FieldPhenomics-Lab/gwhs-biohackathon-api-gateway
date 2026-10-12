# Dataset Plane Decisions and Rule Updates

Date: 2026-10-12 21:20 JST
Topic: settling the dataset plane — image objects in S3, metadata in the gateway — and updating
`AGENTS.md`, `README.md`, and the rule files to match.

## Objective

Resolve the shape of the dataset API before implementing any of it: where images live, where
metadata lives, how a client addresses one item, and what "publishing" means. The session started
from a narrower question (how to upload the GWHD 2021 archive to Hugging Face) and ended by
changing the repo-wide invariants.

## What was done

1. Surveyed the upstreams actually available on the Hub before designing anything, by querying the
   Hub API and `datasets-server`.
2. Settled the dataset plane: S3 holds image objects only; the publisher generates a metadata index
   that the gateway loads at startup and serves from memory; addresses are `(dataset, row)`.
3. Updated the rules and documents:

   | File | Change |
   |---|---|
   | `.agents/rules/project.md` | Architecture invariants rewritten: bytes never traverse the gateway (no `302`), the metadata exception to static-first, single source of truth now the publisher's index, S3 frozen at publish, one id space per dataset, contract frozen for the event. Security: resolve-never-concatenate, and a client URL is parsed but never fetched. Environment: publishing runs where the parquet cache is. |
   | `.agents/rules/gateway.md` | Two surfaces in one process; the dataset surface is index-backed and read-only; no bulk endpoints; every endpoint keyed; the line budget split between the two surfaces. `/v1` references removed. |
   | `.agents/rules/datasets.md` | One command emitting two artifacts; S3 images frozen at publish; byte copy out of the parquet column; row number as identity; the index is a build artifact; never publish the same bytes twice; a partial release gets its own id. The version-prefix rule lost its subject and was replaced. |
   | `.agents/rules/deploy.md` | SSH not exposed (overlay network, SSM fallback); state on a separate EBS volume with a pinned AZ; the S3 write grant is time-bounded to one prefix. |
   | `docs/data-layout.md` | Rewritten from a placeholder into the object-layout, addressing, and index contract. The pending `site`/`stage` question is closed. |
   | `AGENTS.md`, `README.md` | Architecture now describes an image plane and a metadata plane instead of one data plane; layout and publisher descriptions corrected. |
   | `datasets/sources.yaml` | Upstreams and revisions filled in, including the new `gwhs-temp` id. |

## Why the rules changed

Each change was forced by a decision that contradicted an existing rule, which `project.md`
anticipates ("If a rule blocks real work, change the rule in the same PR and explain why"):

- **The metadata index moved out of S3 into the gateway's memory.** The old rule said the gateway
  reads `index.jsonl` from S3 and never caches its own index; the old static-first rule pointed the
  same way. It moved because holding it in S3 meant a *mutable* object on the CDN, which drags in
  cache invalidation, an `index_etag` to detect staleness, and two freshness clocks that disagree.
  Removing that object removed the whole class of problems rather than managing it. The cost is
  that the gateway becomes the only copy of the metadata, which is safe only because it is
  reproducible: it is a deterministic function of the pinned revision, and hand edits are
  forbidden.
- **The `302` endpoint was deleted.** Once clients display images from the public `data.*` URL,
  a redirect endpoint is a second path to the same object. Deleting it also settled the
  `Authorization`-header problem: an `<img src>` cannot carry a key, so with every endpoint keyed
  the image URL has to come from a JSON response rather than from the API.
- **The version prefix was dropped.** It only existed to make in-place overwrites safe. Images are
  written once and metadata is regenerated from a pinned revision, so it bought nothing; rollback
  is a re-run of the same deterministic command. The price is that the contract cannot break during
  the event, which is now stated as a rule instead of implied.
- **Publishing moved onto the API host.** The old rule sent publishing to a temporary instance in
  the bucket's region; with the parquet cache living on the API host, "where the data is" is now
  the API host. The rule's second reason — least privilege on a temporary box — is handled instead
  by a read-only instance role plus a date-bounded write grant on one prefix.
- **`gateway.md` did not cover the dataset surface at all.** Its line budget and its list of in-scope
  concerns described only routing, gating, auth, and cost logging. Left as it was, the first person
  to implement the dataset endpoints would have believed they were violating the rule.

## Evidence gathered

All of the following was measured or read during the session, not assumed.

- **Hugging Face limits** (`hub/storage-limits`): a single file is hard-capped at 500 GB with <200 GB
  recommended; the docs state that very large files are not cached by CloudFront and therefore
  download *slower*; fewer than 100k files per repo and 10k per folder are recommended.
- **`datasets-server` is viewer-grade.** `/rows` rejects `length=101` with `422` (the cap is 100);
  `/filter` returned `500` mid-session because its DuckDB index was being rebuilt.
- **Parquet is range-readable.** `resolve/main/...parquet` answers `302` to the CDN with
  `accept-ranges: bytes` and an `x-linked-size` header, so a parquet-aware client can read part of
  a file — but there is no per-image, per-id URL anywhere.
- **Upstream parquet shapes** (via `first-rows`):

  | Upstream | Row keys | Rows | Bytes |
  |---|---|---|---|
  | `Etienne-David/GlobalWheatHeadDataset2021` | `image, domain, country, location, development_stage, objects` | 6,512 (train 3,655 / validation 1,476 / test 1,381) | 1.26 GB |
  | `GlobalWheat/GWFSS_v1.0` | `image, mask` (512²) | 1,096 | 611 MB |
  | `GWHS/sample_dataset` | `image, image_id, width, height, tags, tags_onehot, objects` | sample | — |

  `gwhd` boxes are `[x, y, w, h]` with a single category (verified: `[936, 4, 41, 78]` at 1024²).
- **The 10 GB figure was an encoding artifact, not a data volume.** The Hub copy is 1.26 GB for the
  same 6,512 images because they are JPEG at 176–268 KB; the Zenodo originals are PNG at roughly
  1.5 MB. The packaging question was therefore secondary to the encoding question.
- **Upstreams duplicate themselves.** `GWFSS_v1.0` also carries a 611 MB zip of its own parquet, and
  `XIANG-Shuai/GWFSS-competition` carries 9.34 GB of zips beside 9.45 GB of parquet. Not a pattern
  to copy.
- **The local GWHD archive is empty.** `~/e/hwang_Pro/data.external/010_globalwheat_2021/` holds an
  empty `images/` directory; the Syncthing folder has not finished syncing.

## Open items

- **`gwhs` may not exist for the event.** The full annotation is still in progress. If it is not
  released before 2026-12-07, and `gwhs-temp` is not publicised, the event runs with two datasets.
  This is the one item that changes the participant-facing scope.
- `/models/list` contents (the model surface, a separate line of work).
- Quota values — implement per-key rate limiting and GPU-second quota first, set numbers from a
  measurement taken before the event.
- Whether the metadata index is regenerated on the host or shipped with the image; regenerating is
  preferred because it is the same deterministic command.
- Not yet done, and deliberately so: no key-set comparison of the bucket after a publishing window,
  so an object planted during that window would not be noticed. Accepted for now.

## Loose ends found while editing

- `docs.md` still describes `/docs` and `/openapi.json` as the API reference path, which remains
  true; no change needed.
- `.github/workflows/` is still empty, so nothing yet enforces any of the above.
