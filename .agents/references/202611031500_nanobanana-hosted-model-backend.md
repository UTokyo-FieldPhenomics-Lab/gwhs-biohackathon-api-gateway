# Nano Banana: a hosted model behind the same backend contract

Date: 2026-11-03 15:00 JST. Branch: `main` (parent and `models`). Worked in
`gateway/src/biohack_gateway/models.py`, `gateway/tests/`, `clients/`, `deploy/`, `docs/`, the
rules, and the private submodule's `models/nanobanana/`.

This is the first model whose compute is not ours: a wrapper on the gateway host calls Vertex AI's
Gemini image models. The design question was not whether it can call Google — a 1K picture took
15 s — but whether a 15-second, paid, non-cacheable call can live in a contract built around a
ten-second, free, cacheable segmentation answer. It can, with five small gateway changes and a
spend gate inside the wrapper.

## Measured

From `gwhs-host` to `aiplatform.googleapis.com`, `gemini-nano-banana-2.1`, 1K:

| what | value |
|---|---|
| TLS handshake | 35 ms |
| total, 1:1 | **15.24 s** |
| total, 16:9 | 15.17 s |
| upstream body | 6.59 MB (4 parts: two thought texts, a thought image, the final image) |
| final PNG | 1.5 MB, 1024×1024 (2.0 MB, 1376×768 at 16:9) |
| final base64 | 2.1 MB (2.7 MB at 16:9) |
| token use | 1120 image, 68 prose, 786 thought; 18 prompt |
| cost, measured | $0.0352 (image $0.0336 + prose/thought), ≈ ¥5.28 |
| least privilege | `cloudresourcemanager.projects.getIamPolicy` answered **403** |

The container then answered the same route over HTTP in **16.17 s**, produced a 1.42 MB PNG in a
1.9 MB body, and appended one ledger line priced at $0.03522 / 1.0 credit.

The thought image is byte-for-byte the same size as the final one; without the filter the answer
doubles. That is a container responsibility, not the gateway's.

## What changed

- **Gateway** (`models.py`): `MAX_RESPONSE_TIMEOUT_SECONDS = 180`; a backend may declare
  `response_timeout_seconds`, the request gets that budget and the backend is passed over for at
  least as long; a backend `429` is relayed as the contract's `429` without retiring the machine;
  `POST /models/{model}/generate` (query only, no body, no cache, takes a lane); `X-Biohack-Team`
  forwarded; byte admission skipped when every backend is loopback.
- **Private submodule** (`models/nanobanana/`): `serve.py`, `vertex.py`, `images.py`, `budget.py`,
  `concurrency.py`, `config.py`, tests, Dockerfile, lock. One process, three path prefixes, one
  upstream limit and one ledger. The reference is reduced to a 2048-side JPEG; the credential is a
  mounted service-account JSON.
- **Clients**: `generate()` in Python, JS and Java, with a 120 s floor; `routes.json` gained the
  route.
- **Deploy**: `deploy/nanobanana.md` (Docker, image transfer, key mount, run line, `models.json`,
  budgets, rotation, troubleshooting); Caddy caps `/generate` at 4 KB.
- **Rules**: `models.md` now describes two kinds of model service; `backend-pool.md` gained
  `vertex-global`; `gateway.md` the five rules; `deploy.md` the container/key/ledger; `clients.md`
  and `docs.md` the method and the family page.

## What broke, and what it taught

- **`Backend.response_timeout` defaulted at import.** The transport tests patch
  `RESPONSE_TIMEOUT_SECONDS`, so a dataclass default captured at class creation ignored the patch.
  The registry now passes the current constant when it builds each `Backend`.
- **The upload queue would have been poisoned.** `predict/upload` holds the byte reservation for the
  whole `forward`, and `LinkRate` divides bytes by that elapsed time. A 90-second edit would drop
  the estimate to the 200 KB/s floor and block every segmentation upload for the next minute. The
  fix is the loopback skip: the queue measures the link to the pool, and `127.0.0.1` does not cross
  it. `internvl-u`'s 15-second edits already pay a smaller version of this cost; the loopback rule
  is for the hosted model only.
- **A backend 429 was a 502.** `_refusal_status` maps everything but 400/422 to 502; a capacity or
  budget refusal would have looked like a broken gateway. It is now passed through, and the machine
  keeps its place.
- **The prompt cannot be a form field.** The gateway forwards the query and the file bytes, not
  multipart text parts. `prompt` rides in the query on all three routes, so the 2 KB transport cap
  is the real limit — about 200 Chinese characters once percent-encoded.
- **Starlette's TestClient now needs `httpx2`.** Added to the private service's check extras.
- **`run_in_threadpool` carries keyword arguments.** The async upload route hands the sync `spend`
  call to the thread pool with them, which keeps the 15 MB decode off the event loop.
- **The image URL is checked twice.** The gateway resolves it through the closed index; the wrapper
  also requires `https` and the dataset host, so a forged body cannot turn the row route into a
  general fetcher.

## Not done yet

- **Deployment.** Docker is not installed on the host and the exposed key has not been rotated.
  Order: new SA key (the pasted one is in the session log), install Docker, `docker load`, the state
  directory, the run line, `models.json`, restart the gateway, reload Caddy.
- **Docs page and fixture.** `models/nano-banana.md` and
  `docs/scripts/fixtures/model-directory.json` wait for a live `GET /models`; the page is a family
  with three `parameters:<id>` blocks, and the fixture must be recorded, not written by hand.
- **Play preset.** The row-edit flow works with the existing page; a text-only preset needs a
  branch in `play.js`.
- **2K and Pro.** Only 2.1 at 1K and 16:9 were measured. 4K is deliberately not offered: its
  base64 can exceed the gateway's 16 MiB answer ceiling.
