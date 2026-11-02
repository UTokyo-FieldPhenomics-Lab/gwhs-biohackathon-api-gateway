# Moving the dataset pins to the `GlobalWheat` org

2026-11-02. Two upstreams moved from a personal Hugging Face account to the project's own
organisation, and the republish that followed found 6,502 `gwhd` images missing from the bucket.

## What changed

The org published copies at `GlobalWheat/GWHD_2021` and `GlobalWheat/GWHS_v1.0_sample`;
`GlobalWheat/GWFSS_v1.0` was already the `gwfss` upstream. `datasets/sources.yaml` now pins:

| Dataset id | Before | After |
|---|---|---|
| `gwhd` | `Etienne-David/GlobalWheatHeadDataset2021@d223a227` | `GlobalWheat/GWHD_2021@976d2f12ca59bff731d3e54713d77bf9aa3bca03` |
| `gwhs-temp` | `GWHS/sample_dataset@655118534e` | `GlobalWheat/GWHS_v1.0_sample@2af7596667715cfcd8185818f30932d1abc35971` |

`docs/data-layout.md`'s dataset table names the same two repositories. Nothing else in the
repository carried the old shas: the recorded quickstart outputs and the figure fixtures do not
print a revision, so no re-recording was needed.

## They are copies, not re-conversions

The Hub's own file metadata settled it without a download and without any local hash: the LFS oids
of the new repositories equal the old ones file for file.

| File | size | LFS oid (both repositories) |
|---|---|---|
| `gwhd/data/test-00000-of-00001.parquet` | 298,290,385 | `74a937e2…6d48412` |
| `gwhd/data/train-00000-of-00002.parquet` | 350,373,033 | `3ada1fb4…043b1bd4` |
| `gwhd/data/train-00001-of-00002.parquet` | 349,049,990 | `21a834ea…98258051` |
| `gwhd/data/validation-00000-of-00001.parquet` | 263,224,769 | `703d391e…4723cd3` |
| `gwhs-temp/data/train-00000-of-00001.parquet` | 11,358,889 | `18c50011…bb02327` |

Splits (3,655 / 1,476 / 1,381 and 10), schema, and license tags are unchanged: `cc-by-4.0` for
`gwhd`, nothing declared for the sample, which is why `gwhs-temp` keeps `license: null`.

## The republish

The checkout on `gwhs-host` fast-forwarded to the commit and `fetch --dry-run` reported **0 to
download** for every dataset, because the cache directory is per dataset id (`cache/<id>/`) and the
file names and contents are identical.

```bash
cd /opt/gwhs-biohackaton-api-gateway/datasets
set -a; . ~/.config/biohack-gateway/publish/env; set +a
uv run biohack-datasets fetch --dry-run
uv run biohack-datasets publish --dataset gwhs-temp   # 0 uploaded, 10 skipped
uv run biohack-datasets publish --dataset gwhd        # 6502 uploaded, 10 skipped — 79 s
```

`gwhs-temp`: revision `2af75966…`, 10 rows, 199 annotation objects. `gwhd`: revision `976d2f12…`,
6,512 rows, 275,371 objects, and 6,502 objects created.

## The 6,502 objects that were not there

`uploaded` means a key was free, so before this run the bucket held **ten** `gwhd` images and the
index advertised 6,512 rows: every other image URL would have answered `404` through
`data.global-wheat.com`. The ten most likely came from an early `--limit 10` rehearsal; the full
index was most likely built with `publish` and no `--bucket`, which uploads nothing and says so
(`uploaded nothing: no bucket configured`) without refusing to install the index.

The republish closed the gap as a side effect. Verified afterwards, with a key, through the
distribution:

```
row 0    -> 200 image/jpeg 278044B  https://data.global-wheat.com/gwhd/images/00000000.jpg
row 3000 -> 200 image/jpeg 199029B  https://data.global-wheat.com/gwhd/images/00003000.jpg
row 6511 -> 200 image/jpeg 164545B  https://data.global-wheat.com/gwhd/images/00006511.jpg
```

`gwfss` row 4 and `gwhs-temp` row 0 still answer `200`; the gateway was restarted and `describe`
reports the new revisions. `gwfss`'s ten-row index is not a defect: the model page states that ten
of its 1,096 annotated rows are published today.

## Do this differently

- **Install only an index whose publish had a bucket**, or accept that the host is advertising rows
  that only a later upload will make readable. `publish` cannot detect this itself: it verifies the
  objects it writes, not the objects an installed index promises.
- **Spot-check three rows through `data.*` after a publish**, not just `describe`. A metadata answer
  says nothing about object presence, and both surfaces pass their own tests while disagreeing.
- The org copy answers a question the old pin could not: a personal account can be renamed or
  deleted, and a deleted repository leaves a commit sha unresolvable even though it is recorded.
