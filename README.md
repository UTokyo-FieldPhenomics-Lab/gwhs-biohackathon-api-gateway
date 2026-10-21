# Global Wheat BioHackathon API Gateway

Dataset and model API for the global wheat BioHackathon (Göttingen, 7–11 December 2026).
Participants fetch images and run segmentation models without downloading a dataset, deploying
containers or owning a GPU.

This README is for maintainers. Participants start at **<https://docs.global-wheat.com>**.
Host provisioning and operations are in [`deploy/README.md`](deploy/README.md); backend operations
are in [`deploy/gpu-backends.md`](deploy/gpu-backends.md). Agents start at [`AGENTS.md`](AGENTS.md).

## Architecture

| Surface | Responsibility | Implementation |
|---|---|---|
| Images | Public image and mask bytes | Private S3 in `eu-central-1`, served through CloudFront with OAC; write-once object paths |
| Metadata | Dataset discovery, facets, annotations and row addressing | Publisher-built SQLite indices loaded into gateway memory at startup |
| Compute | Authentication, inference routing, row-result caching and upload admission | FastAPI on one EC2 host, reaching private Docker GPU backends |
| Contract | API schema, data layout, clients and participant documentation | Pydantic models, [`docs/data-layout.md`](docs/data-layout.md), this repository and its documentation site |

The dataset and model surfaces are implemented and deployed. Deployment evidence and measurements
are [execution records](.agents/references/2026102105_gateway-resource-bound-fixes.md), not a promise
that every backend is online now.

```text
data.global-wheat.com  → CloudFront → S3         public image and mask objects
api.global-wheat.com   → gateway    → backends   authenticated data and inference calls
docs.global-wheat.com  → GitHub Pages            guides, downloads and framed API pages
```

Dataset bytes bypass the gateway: it returns public URLs resolved from the index. Upload inference
is the exception: multipart bodies are temporarily spooled and forwarded, never published or kept
as dataset objects. The gateway resolves `(dataset, row)` by lookup, not by building paths from
client input. A row is absolute within a pinned upstream revision; filtered positions are cursors.
Dataset ids are `gwhd`, `gwfss`, development sample `gwhs-temp`, and `gwhs` when its full release is
pinned. Replacing an index requires a gateway restart; changing the token pool does not.

Model containers and weights belong to the private `models/` submodule. A public clone skips it,
and this repository builds and tests without it. See [the submodule rules](.agents/rules/models.md).
The documentation site also serves client files used by `/play`; retire those consumers together,
not by deleting the site independently.

## Local development

Use your local clone, not the deployment checkout. Python 3.12+ and dependencies are managed with
[uv](https://docs.astral.sh/uv/). There is no root Python package: `gateway/`, `datasets/`, `docs/`
and `clients/python/` have independent environments and lock files. The directory map is in
[`AGENTS.md`](AGENTS.md#directory-structure).

### Gateway

From the repository root:

```bash
cd gateway
uv sync --locked
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
```

Start the app from `gateway/` in its own terminal:

```bash
cd gateway
uv run uvicorn biohack_gateway.app:app --reload
```

`/health`, `/docs`, `/play`, `/openapi.json` and the reference's named assets are public. Other paths
require a participant token, including unknown paths. In another terminal, from `gateway/`:

```bash
uv run biohack-gateway tokens add --id dev       # prints the token once
uv run biohack-gateway tokens list              # team ids only
curl -s localhost:8000/health
curl -s -H "X-API-Key: <token>" localhost:8000/datasets
```

`tokens add --force --id dev` rotates a token; `tokens remove --id dev` revokes it. All verbs accept
`--file` for an isolated pool. The default is `~/.config/biohack-gateway/tokens.json`, re-read on
change. An empty dataset or model directory is expected until local state is supplied:

- Dataset indices: `~/.config/biohack-gateway/index/<dataset>.db`.
- Backend candidates: `~/.config/biohack-gateway/models.json`; the format and private connection
  procedure are in [`deploy/gpu-backends.md`](deploy/gpu-backends.md#4-on-the-gateway-host-putting-it-in-a-models-list).
- Backend credential: `BIOHACK_BACKEND_TOKEN`, matching the containers. Without it, uncached
  inference cannot reach a backend.

Dataset state paths and the public data base URL are `create_app` parameters with defaults, not
environment variables. Runtime overrides are described in the [host runbook](deploy/README.md):
`BIOHACK_MAX_CONCURRENT_PER_TOKEN`, `BIOHACK_CACHE`, `BIOHACK_CACHE_PATH`,
`BIOHACK_CACHE_MAX_BYTES`, `BIOHACK_UPLOAD_SLOTS`, `BIOHACK_UPLOAD_BYTES`, and
`BIOHACK_UPLOAD_WAIT_SECONDS`. They are read at process startup. The default per-team cap is three
inferences, independent of pool width; metadata and row-cache hits take no lane.

### Datasets

The publisher reads pinned provenance from `datasets/sources.yaml`. `fetch` fills a disposable
upstream cache; `publish` validates and builds an index, conditionally uploads images and masks,
verifies new objects, and replaces the index only after success. It never overwrites an object.
An interrupted full publish can be rerun: existing objects with matching size and owned headers are
skipped under the same pinned source. This is not a byte-content comparison or deduplication pass.
Header mismatches stop the run rather than being repaired automatically.

```bash
cd datasets
uv sync --locked
uv run biohack-datasets fetch --dry-run
uv run biohack-datasets fetch --dataset gwhs-temp
uv run biohack-datasets publish --dataset gwhs-temp --dry-run
uv run biohack-datasets publish --dataset gwhs-temp --index-dir ./build
uv run biohack-datasets publish --dataset gwhs-temp --bucket s3://<bucket>
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run pytest -m network                         # optional real upstream checks
```

No bucket means a local index build, with no upload. `--limit 10` is available for rehearsals;
use a separate `--index-dir` so a limited run does not replace a full index. Publishing never
downloads and refuses incomplete shard selections. The normal index destination is host state,
not the checkout. The operator restarts the gateway after installing indices.

Publishing runs on the cache-holding host in the bucket's region and sends upstream image bytes
verbatim. The publisher's write credential is separate from the gateway, scoped to dataset
prefixes and excluded from the service. Its setup is in [`deploy/README.md`](deploy/README.md) §5;
variable names are in [`datasets/.env.example`](datasets/.env.example).

### Clients

The site distributes five files from `clients/`: Python, Java, and a JavaScript core with browser
and Node entries. Python uses NumPy, requests and Pillow; Java uses the JDK; Node resizing uses
sharp. Responses remain native JSON containers; RLE and palette decoding are implemented here,
not separately in tutorials or figure scripts.

```bash
uv run --project clients/python pytest
uv run --project clients/python mypy
uv run --project clients/python ruff check clients/python
uv run --project clients/python ruff format --check clients/python
```

From `clients/js/`, install the check dependencies with `pnpm install` and run `pnpm test`.
Run the Java checks without writing class files into the checkout:

```bash
mkdir -p /tmp/opencode/global-wheat-java-checks
javac -d /tmp/opencode/global-wheat-java-checks clients/java/GlobalWheat.java clients/java/tests/Checks.java
java -ea -cp /tmp/opencode/global-wheat-java-checks Checks
```

`GLOBAL_WHEAT_LIVE=1` enables client live smoke tests and requires a participant key in
`GLOBAL_WHEAT_API_KEY`. Ordinary checks use local fixtures or HTTP servers.

### Documentation site

`zensical.toml` builds `docs/content/` into the ignored `site/` directory. The workflow in
[`.github/workflows/docs.yml`](.github/workflows/docs.yml) builds relevant documentation and client
changes, and publishes main builds to GitHub Pages. Other package checks above are maintainer-run;
the repository does not currently have a separate gateway or dataset test workflow.

```bash
uv sync --locked --project docs
python3 docs/scripts/make_models.py
uv run --project docs zensical build --strict
python3 docs/scripts/sync_clients.py
python3 -m http.server --directory site
```

`zensical serve` is useful for prose changes, but client downloads require `sync_clients.py` after
a full build. Keep the environment and build output outside `docs/content/`.

`examples/quickstart.md` is the notebook's only handwritten source. CI runs `make_notebook.py`
before building, and after successful checks a trusted main run commits only the generated
`docs/content/examples/quickstart.ipynb` back to main for Colab. PR builds never push. A protected
branch must allow this narrow automation; the workflow does not force-push or bypass protection.
`site/` is never committed. Model parameter tables still have a generated-file consistency check.

The quickstart's recorded outputs are a separate, authenticated real-run operation; CI generation
does not execute notebook cells or refresh those outputs. Set `GLOBAL_WHEAT_API_KEY`, then record:

```bash
uv run --project docs --with nbconvert --with ipykernel --with requests --with numpy --with pillow python docs/scripts/record_notebook.py
```

Figure regeneration also uses the clients and requires that key for the published palette:

```bash
uv run --project docs --with pillow --with numpy --with requests python docs/scripts/make_figures.py
```

## Updating dependencies and conventions

Edit the relevant `pyproject.toml`, then run `uv lock` in that subproject and commit its lock file.
Project prose, code and comments are English. Python modules, classes and functions use NumPy-style
docstrings; Ruff and strict mypy are the local quality gates. Keep constraints in
[`.agents/rules/`](.agents/rules/) and execution records in [`.agents/references/`](.agents/references/).

## License

MIT — see [LICENSE](LICENSE).
