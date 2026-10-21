# Gateway resource bounds: repair and deployment

## Execution scope

The user approved repairing the confirmed issues, verifying locally, transferring the tested
revision to the development host, restarting the deployment, and verifying the live service.
The [re-audit](2026102104_gateway-performance-reaudit.md) records the evidence and old-server
baseline. Existing credentials, published indices and backend configuration are preserved.

## Implementation and local evidence

- Cache connection acquisition now precedes the write lock in `put`, `forget` and `prune`.
  Failed transactions roll back before the connection is reused so a later successful commit
  cannot introduce an uncounted entry. Six regression cases failed against the previous cache
  implementation and passed after repair; all 18 cache tests passed.
- A process-wide receive limit defaults to eight uploads, acquired without waiting before
  multipart parsing. Overflow returns 429 with `Retry-After`; every exit releases the slot.
  The per-team inference lane remains a separate fairness gate. The previous worker increase
  to 200 was removed; the default 40 workers leave headroom beyond the eight uploads.
- The parsed `UploadFile.size` reserves link bytes before reading the complete payload.
  Unknown sizes are refused without reading to measure them. Empty bodies and read failures
  return their reservation; a server-side spool read failure is 503, not invalid-request 400.
  All 15 upload-resource regression tests passed, including full receiver refusal before
  parsing and a metadata request while uploads occupy all receive slots.
- Local synthetic upload testing reported 16 simultaneous 14 MB requests with eight receive
  slots: eight completed and eight were refused. Peak process RSS was about 171 MiB;
  with four slots it was about 110 MiB. This measures the synthetic backend path, not GPUs.
  The two retained body copies are bounded by the edge's 15 MB ceiling and receive slots;
  parser buffers, predictions and total RSS need additional headroom and are not part of
  that two-copy calculation.
- TCP connection establishment is now a distinct transport phase: connect timeouts take the
  ordinary backend failover path rather than the upload-send refusal path. A request deadline
  is shared across retries. A socket watchdog covers library-internal header/chunk framing reads
  as well as body reads; it is cancelled and joined before a connection can return to the pool.
  The response body is capped at 16 MiB. This is a resource policy with substantial margin over
  observed outputs, not a proof that every possible RLE or instance result fits; a future legitimate
  larger output requires revisiting the ceiling and host budget together.

## Deployment procedure and verification

The development checkout was clean at `ac1bad9`; its running service predated that checkout.
Transfer uses a git bundle, followed by fast-forwarding the host checkout to the tested commit.
The pre-deployment commit is retained for rollback. The standard `deploy/setup-gateway.sh`
reinstalls from the frozen dependency lock and restarts the service and proxy. No token or
dataset-state file is replaced. Verify route availability and cache behavior, not just the
static package version in `/health`.

The final local gates passed: **294 pytest cases**, strict mypy (14 source files), Ruff lint and
format checks (28 files). Transport tests cover real connect-timeout failover, drip-fed bodies,
status/header lines and chunk framing, oversized answers, a 5 MiB accepted answer, pooled-socket
cleanup and shared retry time. An additional short-`Content-Length` regression caught the fact that
`read1()` does not itself reject a truncated body; the transport now explicitly rejects outstanding
body length at EOF rather than accepting a valid JSON prefix from an incomplete exchange.

Pre-deployment configuration checks: `uv run --project docs zensical build --strict` reported no
issues, `bash -n deploy/setup-gateway.sh` passed, and `caddy:2 caddy validate` accepted the repository
Caddyfile. The host's existing Caddyfile and pre-update revision were saved privately for rollback.

## Observed deployment

- Code commits: `a42570a` (cache) and `dff70c7` (receive/transport bounds); `4b17fd8` records the
  audit corrections and rules. The transferred bundle required the host's existing `ac1bad9` base.
- The host cleanly fast-forwarded to **`dff70c7`**. `sudo bash deploy/setup-gateway.sh` completed,
  and the service process restarted at **2026-10-21 05:21:04 UTC**. `/health` returned 200.
- All **44 targeted cache/upload/transport regression tests passed on the host**.
- No GitHub push was performed: the user requested transfer to the host, which used a private git
  bundle. Existing participant tokens, dataset indices and backend settings were not changed.

## Observed live results

| Check | Result |
| --- | --- |
| Newly available `/pool`, `/models`, `/datasets`, `/health` | all 200 |
| First row prediction | 200, cache miss, 876.2 ms |
| Immediate repeat | 200, cache hit, 2.6 ms |
| Metadata burst, 100 requests, concurrency 1 | all 200, 746 requests/s, p95 1.55 ms |
| Metadata burst, 100 requests, concurrency 8 | all 200, 791 requests/s, p95 13.14 ms |
| Metadata burst, 100 requests, concurrency 32 | all 200, 716 requests/s, p95 50.27 ms |
| Original image upload | 200, 2.03 s |
| Three simultaneous 14,000,000-byte padded images | two 200 (15.10 and 28.83 s); one 429 after 20.16 s |
| Retry after the queue drained, same 14 MB image | 200, 14.18 s; 71 `/pool` probes all 200, maximum 7.95 ms |
| Main-run continuous probes | 219 each of health/datasets/pool, no failures; respective p95 4.3/6.4/5.8 ms |
| Main-run process RSS / host available memory | RSS maximum 73.9 MiB; available memory minimum 1,320.8 MiB |
| Main-run thread and cgroup peak | 16 threads; 97.5 MiB cgroup memory |
| Public HTTPS edge, 5 MB image | 200, 5.77 s |
| Public HTTPS edge, 16 MB image | 413, 76 ms |
| Public HTTPS edge, row JSON over 4 KB | 413 |
| Public `/docs` and `/play` | 200, nonce CSP present; HSTS `max-age=86400` |

The initial post-deploy script's strict `large_uploads_ok` check was **false**, not an all-green
run: it expected three successes despite the deliberate 20-second admission deadline. The 429 was
the expected backpressure when three 14 MB uploads needed more than 20 seconds to drain, not an
internal failure. A separate retry proved recovery; limits were not relaxed to make the test pass.
An initial hand-written public-upload check accidentally used the raw multipart boundary as the
Content-Type and got 400. Correcting the harness to `multipart/form-data; boundary=…` yielded the
5 MB 200 / 16 MB 413 results above; no server change was involved.

After all checks, `/pool` reported **zero bytes in flight and zero waiters**, cached prediction
still returned 200 in 2.7 ms, tmpfs free space returned to 949.1 MiB, host available memory was
1,430.2 MiB, the service was active with seven tasks and `NRestarts=0`. The runtime code and clean
host checkout were both `dff70c7` at verification.

These are short bursts and a few real GPU uploads, not a sustained 50-team capacity certification.
The synthetic receiver-limit tests exercised multiple keys on an isolated gateway; the live token
pool was not expanded merely to fill all eight production receiver slots.
