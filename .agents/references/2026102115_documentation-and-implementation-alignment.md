# Aligning maintainer guidance, rules and implementation

## Scope and decisions

The user requested an audit of `AGENTS.md`, the nine rules files and the maintainer README, then
approved the discussed code and documentation corrections. The agreement is recorded in
the [execution plan](../plans/2026102115_✅documentation-and-implementation-alignment.md).
No host deployment or changes to published objects were requested.

The audit found a fixed three-inference policy described as pool-derived, hashes despite the
no-local-hashing constraint, an unsupported guarantee about equal-size object contents, client
changes omitted from the docs workflow filter, and retired descriptions of model deployment,
client dependencies and environment settings. Rules also carried experiment narratives already
documented in earlier references.

The user identified a notebook publication failure from another session. Its recorded diagnosis
was a stale committed notebook after Markdown changes, not a kernel failure. The existing workflow
already ran the converter and deliberately failed its notebook diff. The chosen replacement keeps
the GitHub-hosted Colab target but synchronizes that generated file automatically after validation.

## Changes and evidence

- Canonical request text replaces SHA-256 keys, without a schema migration or clearing host state.
  Old digest-keyed entries cold-miss and remain subject to the existing capacity pruning. A new
  persisted-key test failed on the old implementation; cache tests pass after the change. The
  legacy-entry regression uses a literal opaque digest-shaped fixture, not a hash computation.
- The publisher uses conditional creation, verifies new objects, and checks existing size and owned
  headers for resumability under the same pinned source. Header repair is removed. Before changes,
  an in-memory reproduction using the actual publisher functions returned `skipped` for equal-size
  different contents, confirming that the former stronger prose was unsupported.
- Figure generation now reads the published GWFSS palette through the client and calls the client's
  palette decoder in both rendering paths. The offline changed-palette test first failed with the
  old two-argument figure interface, then passed with both client decoder calls observed. Existing
  rendered images were not regenerated; generation now needs a participant key for the declaration.
- The docs workflow includes client-only changes. The trusted main sync job downloads the notebook
  artifact from the successful build, checks that main still equals the build source, and stages
  only `docs/content/examples/quickstart.ipynb`. A normal push must succeed before deployment;
  neither a fork PR nor a PR job has a repository-content writer. No force push or personal token
  is introduced. The same run publishes its verified Pages artifact because a `GITHUB_TOKEN` push
  does not start a second push workflow.
- `AGENTS.md`, rules and the README describe the implemented surfaces and their upload exception.
  Runtime guidance includes cache and admission settings. The README addresses maintainers;
  participant and host-operations entry points remain links to their own material. The docs site
  remains an active download and playground dependency, requiring coordinated retirement.

## Validation

- `uv run pytest`, `uv run mypy`, `uv run ruff check .`, `uv run ruff format --check .` in
  `gateway/`: 296 tests passed; types, lint and formatting passed. The installed FastAPI test client
  emits an existing Starlette/httpx deprecation warning.
- The same gates in `clients/python/`: 31 passed, 2 live tests skipped; types, lint and format passed.
- The same gates in `datasets/`: 117 passed, 5 network cases deselected; types, lint and format
  passed. Create-first ordering, header conflicts and readback-token mismatches have regressions;
  the new publisher tests failed against the baseline implementation before repair.
- `node --test tests` in `clients/js/`: 29 passed, 4 sharp-dependent checks skipped because sharp
  is not installed in this checkout.
- The README's `javac` and `java -ea` commands passed all Java checks, placing class files under
  `/tmp/opencode/global-wheat-java-checks`; javac reports the existing unchecked-operation note.
- `make_models.py`, `zensical build --strict`, `sync_clients.py` and the model-page diff check
  passed. The converter's in-memory result passes its validation and matches all 26 tracked cells
  read through verified `nb 0.0.9`; no manual notebook JSON edit was made.
- `actionlint 1.7.12` passed. Upload-artifact v7 and download-artifact v8 tags were verified against
  their upstream repositories. An isolated git simulation covered unchanged output, notebook-only
  commits, successful normal push and rejection after main advances. `bash -n` passed for the host
  scripts and `git diff --check` passed.

## Historical detail moved out of rules

The old documentation rule recorded that the recorded-answer panel loaded an ES module with a
classic script tag, failed to parse, and was subsequently replaced by a generated figure. Keep
that failure as historical context here; the active constraint is module loading and decoder reuse.
Other removed burst, response-time, palette-table and VRAM narratives already have linked records.
The backend table now labels location groups rather than claiming different laboratory routers
prove independent power/uplinks, and no longer repeats the superseded 16 October SSH refusal as
current status. No machine was contacted to infer a new status.

## Operational follow-up

At local validation time, the workflow had not run on GitHub from these changes. Repository/organization token
settings and main branch protection must permit the narrow bot commit; a rejection intentionally
blocks publication rather than bypassing policy. Local verification does not establish those
account settings. The private `models/` checkout is empty and was not populated or audited.

A separate quickstart execution issue was identified while reading with `nb`: step 1 constructs
`Client()` before step 2 collects a key. A clean environment therefore fails before the prompt.
This was added to the project backlog; repairing it also requires recorder-owned output updates,
not just notebook generation. It is not hidden by claiming the CI build executes notebook cells.

## Quickstart credential-order follow-up

The user subsequently approved fixing that issue. The first Python cell now imports the module and
prints the class name without constructing a client. Step 2 prompts when the key is missing or
empty, then constructs the client. The printed `Client` output is unchanged and was reproduced by
the setup-cell tests; no recorded output region or data result was edited by hand.

Three parameterized cases execute the actual Markdown-to-notebook setup cells against the real
client, with a stubbed private prompt and no network calls. Missing and empty credentials failed
before the fix; all three cases now pass, including the existing-key path without a prompt.
The Python client suite reports 34 passed and 2 live tests skipped, with mypy, Ruff lint/format,
the strict docs build, converter validation (26 cells) and diff whitespace checks passing.
The tracked notebook remains CI-owned and will be regenerated/synchronized after the changes are
pushed; no manual notebook edit, commit or deployment was performed.

After local validation, the user authorized committing and pushing the changes to GitHub. Commit
groups separate gateway caching, dataset publishing, quickstart setup, figure reuse, the docs
workflow and maintainer guidance. Each staged group is checked for credential-shaped literals and
machine-address literals before committing; generated `site/` files remain excluded.
