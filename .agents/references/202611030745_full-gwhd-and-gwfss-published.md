# The full `gwhd` and `gwfss` sets are in the bucket

On 2026-11-03 the two released datasets were published in full from `gwhs-host` (the SSH line is in
the untracked `.agents/local/pool.md`), and the gateway was restarted to load both indices.
`gwhs-temp` is untouched and still the ten-row sample.

## What ran

On the host, in `/opt/gwhs-biohackathon-api-gateway/datasets`, with the publisher's own
environment (`set -a; . ~/.config/biohack-gateway/publish/env; set +a`):

```
uv sync --locked
uv run biohack-datasets publish --dataset gwfss --bucket $BIOHACK_BUCKET
uv run biohack-datasets publish --dataset gwhd  --bucket $BIOHACK_BUCKET
sudo systemctl restart biohack-gateway
```

The upstream cache `datasets/.hf_cache` (1.8 GB) already held every pinned parquet, so no `fetch`
was needed: 4 files for `gwhd` (1.26 GB) and the 2 `labelled-*` shards for `gwfss` (611 MB).

| Dataset | Result line | Rows after publish |
|---|---|---|
| `gwfss` | `2172 uploaded, 20 skipped` — 79 s | 1096, 4 classes, facets head 739 / stem 847 / leaf 1080 |
| `gwhd` | `0 uploaded, 6512 skipped` | 6512, splits train 3655 / test 1381 / validation 1476, 275 371 boxes |

`gwhd` was already complete: the 2026-11-02 run
([org-owned upstream pins](2026110216_org-owned-upstream-pins.md)) had uploaded 6502 of 6512 images,
and today's pass confirmed the remaining 6512 keys with matching size and owned headers. That
confirmation is what the publisher's skip rule measures — presence, size and the two headers it owns,
not byte equality.

`gwfss`'s 2172 objects are 1096 images plus 1096 semantic masks, i.e. the 20 skipped were the ten
prefix rows already published on 2026-10-15 (image + mask each).

## Verification

Public reads through CloudFront, no key:

```
gwfss/images/00000000.png  200 image/png   593758
gwfss/images/00001095.png  200 image/png  1053911
gwfss/masks/00000000.png   200 image/png    16324
gwfss/masks/00001095.png   200 image/png    24050
gwhd/images/00006511.jpg   200 image/jpeg
```

Authenticated metadata after the restart: `GET /datasets/gwfss` → `rows: 1096`,
`GET /datasets/gwhd` → `rows: 6512`. `/health` answered `502` for the first few seconds after the
restart and `200` after that; that transient is the TLS/uvicorn start, not a failure.

Index files now: `~/.config/biohack-gateway/index/gwfss.db` (104 KB) and `gwhd.db` (6.2 MB), written
by a publish that had a bucket — the condition [datasets rules](../rules/datasets.md) put on
installing an index at all.

## What is now stale

The participant site and the runbook still describe a ten-row prefix, because they were written
while the full sets were being prepared:

- `deploy/README.md` §"What `index/` holds today" — "the first ten rows of each dataset".
- `docs/content/datasets/index.md`, `docs/content/index.md` — "first ten rows".
- `docs/content/models/gwfss-segformer-b4.md` — "Ten of the dataset's 1,096 annotated rows are
  published today".
- `docs/content/examples/quickstart.md` — the recorded `datasets()` output lists `10 rows` per
  dataset; the recorded regions are the recorder's to rewrite
  ([docs rules](../rules/docs.md)), so a docs pass needs `record_notebook.py` with a key, not a
  hand edit.

## Next time

Run `publish` under `nohup` with a log file from the first attempt. A foreground run over ssh dies
with the connection and takes its own summary line with it — the `gwhd` pass had to be repeated only
to read `0 uploaded, 6512 skipped`, and a repeated pass over an unchanged revision is the cheap way
to recover that number.
