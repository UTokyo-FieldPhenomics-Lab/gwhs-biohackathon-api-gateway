# Data Plane Region, Streamed Publish, and the Size+ETag Question

Date: 2026-10-13 15:00 JST
Topic: three decisions taken after the fetch step landed — the bucket goes to Frankfurt, the publish
streams objects to S3 instead of staging them, and what is left of the size + `ETag` mechanism after
the earlier zero-hash decision. Also the numbers that make the first two obviously affordable.

## Objective

Answer whether the publish can run on the API host's 37 GiB root volume at all, and close the
question of whether the publisher stages the converted corpus on disk. The answer turned out to
include a fourth thing: a runbook, because the bucket, distribution, certificate, and DNS record had
never been written down.

## Measured

All of it from ranged parquet-footer reads, so the measurements cost about 100 KB rather than 1.9 GB.

| dataset | files | rows | parquet | `image.bytes` | `mask.bytes` | S3 objects |
|---|---|---|---|---|---|---|
| `gwhd` | 4 | 6,512 | 1260.9 MB | 1258.6 MB | — | 6,512 |
| `gwfss` | 2 | 1,096 | 610.8 MB | 595.5 MB | 15.2 MB | 2,192 |
| `gwhs-temp` | 1 | 10 | 11.4 MB | 11.3 MB | — | 10 |
| **total** | **7** | **7,618** | **1883.1 MB** | 1865.3 MB | 15.2 MB | **8,714** |

SNAPPY does nothing to these bytes (gwhd's `image.bytes` is 1258.6 MB compressed and uncompressed),
so the payload leaving for S3 is the parquet's own size: 1881 MB. Per object: gwhd ≈ 193 KB, gwfss
images ≈ 543 KB and masks ≈ 13.9 KB, gwhs-temp ≈ 1.13 MB.

**Row groups are the memory unit, and they are fine-grained:**

| dataset | file size | row groups | rows/group | payload/group |
|---|---|---|---|---|
| gwhd | 263–350 MB | 14–19 | ~96 | 4.4 – 22.5 MB |
| gwfss | 258 / 352 MB | 6 | ~91 | 19.5 – **107.9 MB** |
| gwhs-temp | 11.4 MB | 1 | 10 | 11.3 MB |

The largest single row group is 107.9 MB (`gwfss/labelled-00001`). A reader's peak is therefore one
row group, **not** the 350–600 MB a whole-file `read_table` would cost.

## Decided

| Decision | Reason |
|---|---|
| The bucket is in `eu-central-1`, the region that publishes into it | It removes the cross-region hop measured at 226–256 ms RTT (`.agents/references/202610122311_ec2-gateway-host-bringup.md`), removes the egress charge, and lets the gateway host stay where the cache is. Participants are in Göttingen, so a European origin is also the better CDN origin. |
| The publisher **streams**: read a row's bytes, `PUT`, release | Peak disk is the 1.88 GB cache plus nothing, so no scratch volume and no teardown step exist. Staging would fit (3.8 GB against 37 GiB) and is still not worth it: a staged copy is a second copy that can disagree with the cache for no gain. |
| Reads serial, uploads concurrent | One row group is the ceiling (~108 MB); the Hub-style `use_threads=True` default or parallel file readers would multiply it by the number of readers. Concurrency belongs on the upload side, where each worker holds one ≤1.2 MB object. |
| Every object is a single-part `PUT` | Objects are ≤1.2 MB, and single-part is what keeps `ETag` equal to the content MD5, which is what the size + `ETag` comparison rests on. Multipart would silently replace it with a digest of the parts. |
| The publisher sets `Cache-Control` and `Content-Type` per object | S3 stores no type, and a viewer without a cache lifetime revalidates every request even when CloudFront holds the object. |

Memory budget on the 2 GiB host, for the record: Python + pyarrow ≈ 150 MB, one row group ≤ 108 MB,
16 upload buffers ≈ 8 MB, and the gateway process with its resident index alongside — under 500 MB
with the reader bounded as above.

## The size + ETag question, settled

The recollection was that `size + ETag` had been deleted during the API design. It had not. What the
zero-hash decision deleted (`.agents/references/202610090835_initial-scaffold.md`) was `sha256`
everywhere, the `digest` field in `sources.yaml`, a self-maintained `SHA256SUMS`, the manifest's
`dup_sha256` counter, and — the one that probably caused the recollection — **`index_etag`**, which
disappeared when the metadata index left S3 (`202610122120`). S3's object `ETag` is a different
thing and is what the rules still sanction: `project.md` names it among the platform tokens that
replace a content hash, and the same reference marks `datapack.md`'s ETag line "already aligned".

Two gaps in the wording were closed rather than the mechanism:

1. `design-philosophy.md` said "Compare local size + MD5 against remote size + `ETag`", which reads
   as computing a local digest and contradicts `datasets.md`'s "Do not hash locally". The local side
   is a **size only** (the length of the byte string taken out of the parquet column); both digests
   are S3's, one from the `PUT` response and one from a later `HEAD`.
2. Neither file said **where** the comparison applies. It applies to objects in the publisher's own
   prefix and nowhere else: code identity is a git tag or commit, dependencies are `uv.lock`, and
   data identity is the pinned upstream revision. None of the three is ever checked by comparing
   bytes, which is the failure the wording now forbids outright.

## Also done

- `deploy/data-plane.md`: the missing runbook — bucket, origin access control, distribution,
  bucket policy, certificate, DNS record, the two headers the publisher must send, verification with
  one hand-placed object, and the shape of the cost.
- `deploy.md`: rules for the data plane (private bucket, one reader, write-once means no
  invalidation, bucket region follows the publish, and why this plane is built by hand while the
  host scripts stay idempotent).
- `AGENTS.md`, `README.md`, `deploy/README.md`, `project.md`, `datasets.md`: the `ap-northeast-1`
  mention is gone, the publishing rule now says "in the bucket's region, and streams", and the
  publisher rules gained the streaming, single-part, and per-object-header constraints.
- The GWFSS footer comparison in `202610130100` was re-run as a plain byte comparison (16155 B and
  16952 B, identical) so that no digest of our own appears even as evidence.

## Verified

- Docs-only change: `cd datasets && uv run pytest` (14 passed, 4 deselected), `uv run pytest -m
  network` (4 passed), `uv run mypy`, `uv run ruff check .` unchanged.
- Footer byte comparison over range GETs, six GWFSS files: sizes equal, footer bytes equal.

## Open items

- The publisher does not exist yet; the streaming shape, the two headers, and the single-part `PUT`
  are rules and a runbook until it does.
- The runbook has not been executed: no bucket, certificate, or distribution exists for either
  environment. `data.*` is still uncreated, and `api.*` still has no TLS in front of it.
- Whether a publish runs while the gateway serves. Memory says yes (< 500 MB); 2 vCPU says publish
  before the event, when the gateway is not answering.
