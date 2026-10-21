# Documentation and implementation alignment

## Steps

- [x] Remove local hashes and simplify the write-once publisher with regression tests.
- [x] Reuse client palette decoding in figure generation and verify it offline.
- [x] Generate and synchronize the notebook in CI, including client-only publication changes.
- [x] Update AGENTS, rules, runtime guidance and the maintainer README.
- [x] Run the affected quality gates and record the results.

## Agreed scope

Use canonical request text as the cache key; do not migrate old disposable cache entries. Replace
MD5 in the test object store with opaque simulated ETags. Keep publisher resumability, conditional
creation, pinned provenance, complete-shard validation, atomic index replacement and verification;
remove automatic header repair and state the actual size/header comparison guarantee.

The notebook remains tracked at `docs/content/examples/quickstart.ipynb`. CI generates it and,
after successful validation on main, commits only that generated file without force-pushing or
bypassing branch protection. Pull requests never write repository contents. `site/` stays ignored.

Rules retain constraints, short reasons and evidence links; execution narratives belong in
references. The docs site is an active client-download and playground dependency, not disposable
independently. The README is for repository maintainers. Do not deploy to the host, change model
containers, or delete existing published objects.
