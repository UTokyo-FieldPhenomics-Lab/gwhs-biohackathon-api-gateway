# Gateway performance and severe-bug re-audit

## Scope and baseline

The user authorized testing the real development server. No service restart, deployment, network-policy change, token change, or GPU shutdown was performed for this review. Local fault reproductions used bounded disposable processes and synthetic backends; these are not measurements of GPU throughput.

- Local reviewed HEAD: `412dadb`.
- Development server checkout: `ac1bad9`; results for that server must not be attributed to the newer local code.
- Initial server observation: 1,936 MiB RAM, no swap, 1,454 MiB available. `/tmp` is tmpfs (969 MiB total, 952 MiB free), not the root disk. Root filesystem has 34 GiB free.
- Service initially active: approximately 45.7 MiB cgroup memory, 61.6 MiB recorded peak, seven tasks.

## Confirmed local regressions and risks

### P1: cache recovery can permanently deadlock the shared cache lock

`gateway/src/biohack_gateway/cache.py:328-329` acquires a non-reentrant lock before obtaining a thread-local connection. A thread without a connection enters `_connection` (line 486), then `_open`, which tries to acquire the same lock again (line 512).

Reproduction 1: construct the cache on the main thread, then call `put` from a fresh daemon worker. The worker remained blocked after one second.

Reproduction 2 matches the row handler's normal read-then-write sequence: inject one transient `sqlite3.OperationalError` into the worker's first connection open. `get` correctly returns a cache miss; the backend can then answer; `put` retries opening the connection and deadlocks. A concurrent `stats()` also remained blocked. Captured stack: `put:329 -> _connection:486 -> _open:512`.

Ordinary successful `get` initializes the worker's connection before `put`, which explains why happy-path tests miss this. The danger is recovery from a cache-open failure, not that every prediction currently deadlocks. The held lock also blocks cache hit/miss bookkeeping, `/pool` statistics, and cache shutdown. This is a deployment blocker for the local cache-counter change.

### P1: pre-admission upload reads retain the complete payload in memory

`models.py:2102` reads the entire spooled file into `payload`; `uploads.acquire` is only called at line 2110. Waiting requests retain that bytes object. The earlier assertion that spooling removes the heap exposure was incorrect.

A local reproduction called the actual upload handler with three already-rolled `SpooledTemporaryFile` objects and blocked its admission seam. Each waiting handler retained 13,999,994 bytes. No upload had been admitted. RSS increased from 55,508 KiB to 96,684 KiB (about 40.2 MiB), matching the retained payloads. Temporary files were closed afterwards.

At the proposed 50 teams × 3 uploads × 15 MB, payloads alone would approach 2.25 GB. This is a capacity calculation, not an OOM measurement. On the development host, the spooled files additionally occupy tmpfs; its capacity may be exhausted before this theoretical concurrency is reached. Raising the worker limiter to 200 is not an adequate resource-isolation fix.

For a minimal correction, admission can use the parsed `UploadFile.size` before the whole-file read (with explicit handling of unknown sizes), rather than trusting client `Content-Length`. This does not itself bound multipart spooling before the handler, so that resource must be budgeted separately.

## Evidence handling

All credentials stayed in the host's credential files and were not printed. No host addresses or raw service logs belong in this record. Local reproductions used `gateway/.venv/bin/python -B` with stdin programs, temporary SQLite files under `/tmp/opencode`, and daemon threads inside short-lived subprocesses for deadlock probes. A first upload-harness attempt omitted required request URL scope fields and failed before admission; the corrected harness supplied them and produced the measurements above.

### P1/P2: connect timeouts are mistaken for upload-send timeouts

In `models.py:640-654`, the phase is `send` before `connection.request`, but that call also opens a new TCP connection. A `socket.create_connection` timeout is therefore returned as `stage="send"`. `forward` (lines 1414-1423) immediately returns 504 without trying a healthy fallback or marking the failed attempt unhealthy.

A bounded local fault injection reproduced this through `ModelRegistry.forward`: first backend connect timed out, second backend was a reachable localhost HTTP service. Outcome was 504, failed backend `unhealthy_until=0`, second backend received zero requests. This affected a row prediction too; the returned sentence incorrectly described an upload. A later failed health probe can exclude the backend, so this is a broken request-level failover, not a permanent inability to recover.

### P2: upstream response consumption has neither a total deadline nor a size ceiling

`models.py:645-649` sets a socket timeout then calls `response.read()` without a byte limit. A local server sent 15 bytes one byte every 0.04 seconds. With a requested timeout of 0.1 seconds, transport still returned 200 after 0.562 seconds. A stalled response correctly timed out after 0.101 seconds. This demonstrates an inactivity timeout, not a total exchange deadline. An 8 MiB response was also accepted without a limit.

This requires an abnormal backend/proxy response; it is not evidence that ordinary participants can independently make the backend emit arbitrary bytes. Nevertheless a malfunctioning backend can retain a worker and grow memory despite the purported response budget. Both the total duration and response size need independently chosen bounds compatible with legitimate model output.

## Findings deliberately not promoted to confirmed severe bugs

- The 500 counter remains high after quarantine and resets on success. A frozen-clock reproduction confirmed this, but that is consistent with the current counter's documented semantics; it is not by itself a new high-severity bug. Retrying the third failing request on another backend remains a policy question because it can spread request-specific failures.
- A quarantine period shorter than total upload time is not sufficient evidence of a functional defect. Do not manufacture a high-severity issue merely from an overbroad documentation invariant.
- A malformed configured URL is encountered during startup declarations as well as periodic probes. It is not, without an additional runtime trigger, proof that an otherwise-running service's probe loop can be remotely killed.

## Development-server measurements before repair

The baseline ran 2026-10-21 04:51–05:00 UTC. The running process was older than the checkout: it started before the checkout update, `/pool` returned 404, and repeated predictions lacked the `cached` field. Its exact loaded commit cannot be proven from the static `0.1.0` health version; startup timing and response/log features are consistent with `ea576ec`. These measurements must not be represented as tests of `412dadb` or even of the checkout's `ac1bad9`.

| Scenario | Observed result |
| --- | --- |
| Loopback metadata, sequential | p50 about 1.0–1.9 ms |
| Mixed metadata, 100-request bursts, concurrency 1 | 825–841 requests/s; p95 1.7 ms |
| Mixed metadata, 100-request bursts, concurrency 8 | 817–877 requests/s; p95 12.5–13.4 ms |
| Mixed metadata, 100-request bursts, concurrency 32 | 581–788 requests/s; p95 65–111 ms |
| Row detail, about 8.5 KB, concurrency 32 | 500 requests/s; p95 98.1 ms |
| Real GPU predictions after warmup | roughly 0.60–1.33 s |
| First observed call per model | 11.48 and 12.16 s, including an approximately 10.25 s failed attempt and successful fallback; backend cold-start causation was not independently established |
| Three concurrent predictions plus 20 metadata probes | all 200; metadata p50 25.4 ms, maximum 28.0 ms |
| Memory and recovery | process RSS peaked around 69.6 MB; host available memory stayed above 1.45 GB; no service restarts |

The client-to-public-endpoint path had approximately 0.3 s median latency and multi-second tails, unlike host-local HTTPS. A 20-packet ICMP sample lost two packets. This suggests a path contribution but does not prove that every HTTP tail was caused by packet loss.

Limits: only ten rows per deployed dataset, short bursts rather than sustained saturation, one participant key, 13 real GPU calls, no uploads, no working `/pool` or cache on the running version. The observed one-core CPU usage and throughput plateau do not independently isolate the GIL from other serialization costs. No 50-team capacity claim follows from these tests.
