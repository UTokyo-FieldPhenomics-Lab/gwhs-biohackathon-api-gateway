# An audit's four findings: three fixed, one written down

2026-10-21. An independent audit of `ac1bad9` — before the key-page policy in
[`2026102103_key-page-csp-and-hsts.md`](2026102103_key-page-csp-and-hsts.md) — reported four
weaknesses and was careful about what it had not shown: no unauthenticated access, no code
execution, no host takeover. Every code path it named was checked here and the paths are real. Three
were worth closing for an event that lasts five days; the fourth is a wording problem.

## What changed

- **The row route has its own body limit.** `POST /models/{model}/predict` takes
  `{"dataset": …, "row": …}`, about forty bytes, so `deploy/Caddyfile` caps it at `4KB` while the
  upload route keeps `15MB`. Why it mattered: FastAPI parses the body before the handler, and the
  middleware charges a lane only for uploads — so a 15 MB body of integers allocated ~150 MB of
  objects and blocked the single worker's event loop for a quarter second, to be answered `400`.
  The limit sits in Caddy because that is where bytes are counted as they stream, which is the only
  check a chunked body cannot dodge, and because `deploy/Caddyfile` is already the one place the
  upload's number is written down.
- **A repeated filter is one condition.** `_filter_pairs` drops duplicates, keeping arrival order,
  and refuses more than `MAX_FILTERS` (`32`) distinct conditions with `400`. Selection is rows times
  conditions, and the query string is the one part of a request a client sizes freely: a thousand
  copies of `split=train` measured 104 times the work of one, for the same page. Deduplicating also
  means a form or a client that appends a condition twice is not charged, or capped, for it.
- **Forget reaches the other tab.** `api_key.js` read `null` both for "nothing stored" and for
  "storage refuses to answer", then fell back to the in-memory copy — so a page told that another
  tab cleared the key re-armed Swagger with the copy it still held. `read()` now returns
  `undefined` for unreadable, `null` for absent; the storage listener sets the held copy from the
  store, including to empty. A store that answers "nothing" is an answer.
- **The cache budget says what it counts.** `max_bytes` was already documented as body bytes; the
  entry also stores the request it is keyed by (this repository keeps no content hash) and SQLite
  keeps its own pages, so the file is larger — by hundreds of times when answers are tiny and
  queries are long. That is now stated where the setting is read and where `/pool` publishes the
  figure, and nowhere is `BIOHACK_CACHE_MAX_BYTES` called a disk quota.

## What was measured

- `caddy validate` in the official image: **Valid configuration**, matcher form included. The
  syntax form `request_body @matcher { max_size … }` is supported by the 2.x build.
- A local Caddy 2 with the same directives in front of a stub that logs what it receives:

  | Request | Answer | Bytes the upstream received |
  | --- | ---: | ---: |
  | 3 KB to `/models/x/predict` | 200 | 3 003 |
  | 2.8 MB to `/models/x/predict` | 413 | 4 000 |
  | 2.8 MB chunked to the same | 413 | 4 000 |
  | 5 MB to `/models/x/predict/upload` | 200 | 5 000 008 |
  | 16 MB to the same | 413 | 15 000 000 |
  | 5 MB to `/datasets/gwhd/images` | 200 | 5 000 008 |

  So the row route's ceiling is enforced mid-stream (the origin sees 4 000 bytes, not 2.8 MB), the
  upload's 15 MB is untouched, and other routes are unaffected.
- `uv run pytest` — **253 passed**; mypy, `ruff check`, `ruff format --check` clean. New tests:
  a repeated filter returns the page one copy returns; 33 distinct filters are refused as
  `invalid_request`.
- **Two tabs of `/play`, in Firefox, against a local server:** tab B cleared the store; tab A's
  `apiKeyStore.get()` returned `""` and its status line fell back to "send a participant token in
  the X-API-Key header". With `Storage.prototype.getItem` made to throw instead, the held copy still
  answered — the fallback it exists for still works. No console messages.

## What remains, and what was learned

- **Uploads still read before the byte budget admits them.** `predict_upload` calls
  `file.file.read()` and only then `uploads.acquire`, so the 24 MB budget is not a bound on peak
  memory: the bound is three lanes per team at 15 MB. One team is 45 MB and harmless; fifty teams
  are 2.25 GB and are the event's whole attendance. Left as it is, because moving the reservation
  before the read means reserving on `Content-Length` and reconciling afterwards — a change to the
  admission contract, not a line.
- **`pkill -f` matches the shell that runs it.** A pattern written literally kills the invocation
  itself, which looks like the tool dying. Write `pkill -f 'stub[.]py'`, or kill by pid.
- **A dead stub looks like a broken limit.** Caddy dials the upstream before the body limiter sees
  any byte, so with the stub gone every request answered `502` — including the 2.8 MB one that had
  answered `413` a minute earlier. The `413` is only produced once there is something to proxy. Read
  a `502` as "the harness died", not as "the limit stopped applying".
- **One 413 message now covers two limits.** The old text told the reader their *image* was over
  15 MB; a row request over 4 KB would have received the same advice about resizing a picture. The
  message names both cases and the upload's number, because a participant reads one error shape
  from one client.
- The audit's artifacts (`.agents/references/2026102111_security-audit/`) are not in this checkout
  and the working tree was clean, so its scripts were not re-run here; the findings were verified by
  reading the paths it cited and by the measurements above.
