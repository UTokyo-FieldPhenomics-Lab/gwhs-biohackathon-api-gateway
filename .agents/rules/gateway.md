# Gateway Rules

Package: `gateway/`. Repo-wide invariants are in [`project.md`](project.md) and are not repeated
here.

- **Stack**: FastAPI + uvicorn + Pydantic v2, deployed with systemd. Docker is an escape hatch for
  hosted platforms only; the gateway does not run in a container.
- **Packaged, not run from source.** The gateway is an installable package (`src/biohack_gateway`)
  launched as `uvicorn biohack_gateway.app:app`; no code depends on the working directory.
- **Every request is key-authenticated, and the gate is closed by default.** One middleware checks
  `X-API-Key` against the token pool before routing, so an endpoint added later is protected by
  existing rather than by remembering a decorator, and a path that does not exist is refused too. The
  exceptions are named in the code — `/health` and the two pages the documentation site frames —
  because they carry no data and could not be opened in a browser if the page itself needed a header.
  An absent pool is a closed gateway rather than an open one, and a pool that has never parsed answers
  `503 unavailable` until it does. A token may have three requests in flight and no more — the pool's
  own width — refused as `429 quota_exceeded` rather than queued; a per-key request *rate* is still
  absent. [`project.md`](project.md) holds that decision and its triggers.
- **Images reach clients through the public `data.*` plane**, so nothing has to be anonymous.
- **Routing is a sequential retry over interchangeable backends.** Try backends in order, **idle ones
  first and then the shortest queue** — a backend is only known to be unhealthy *after* an attempt
  fails, so without the first a burst arriving during the failure queues behind it, and without the
  second a burst deeper than the pool is answered by whichever backend the host listed first: three
  cards and 160 simultaneous calls put 140 on one of them, so the pool ran at one card's rate
  ([measurements](../references/2026101714_what-a-burst-does-to-the-pool.md)). Mark a failed one
  unhealthy, and move on. The window a failed backend is passed over for must be **at least the
  response budget the failed attempt had**, and `models.py` asserts the invariant, because a window
  shorter than the failure it remembers charges the same wait again for every window. A backend is
  any Docker-capable GPU resource behind one uniform protocol; the gateway never special-cases a
  provider. No circuit-breaker and no weighted routing: the answers are interchangeable and a queue
  is not this layer's job.
- **A handler that reaches a backend is a plain `def`, never `async def`.** The transport is a
  blocking socket call, so an async handler runs it on the event loop and holds every other route
  for the round trip — an upload did exactly that, and `/health` answered in 11 s under six of them
  ([the same measurements](../references/2026101714_what-a-burst-does-to-the-pool.md)). FastAPI runs
  a `def` handler in its thread pool, which is the concurrency this surface wants; `predict_upload`
  reads its body through `file.file` for that reason.
- **The pool is probed on a clock of its own, and the count carries that clock.** One task asks every
  backend's `/health` every `PROBE_INTERVAL_SECONDS`, off the request path, and `backends_up` counts
  what that probe last saw — not "has not failed yet". Without it the count lies twice: a machine that
  dies quietly stays up until somebody calls it, and a machine that was off when the process started is
  published as up because nothing has failed against it. The age is published with the count
  (`checked_seconds_ago`), because a verdict without its age is a promise about the past, and both
  pages show it; the task is cancelled with the process, so a reload never leaves two tasks asking the
  same machines. The probe asks *whether*, never *what*: a declaration is a specification, and one
  that changes under a running process is a deployment that has not been restarted.
- **The model surface is four routes, and its error mapping decides what a caller retries.** `GET
  /models` and `GET /models/{model}` answer what the pool holds; `POST /models/{model}/predict` takes
  an address and `POST /models/{model}/predict/upload` takes bytes, because one handler branching on a
  content type would have two jobs. A backend's `4xx` is the request's fault: it is re-worded into this
  contract's error shape and never retried. A backend's `5xx` or a timeout is the backend's fault: mark
  it unhealthy for at least as long as the attempt's own budget and try the next, so a caller never
  learns there was more than one
  machine. A backend refusing the gateway's own token is neither — it is this side's misconfiguration
  and answers `unavailable`, because telling a participant their key is wrong sends them to look in
  the wrong place.
- **A model's control parameters are the model's, never the gateway's.** The container declares what
  it accepts, with defaults and bounds, in its own `/health`; the gateway repeats that declaration from
  `GET /models` and forwards a request's values verbatim without knowing their names. That is what
  keeps a new model to a configuration change — and it is why nothing here may name a parameter: a
  `prompt` written into this package would be a second place the model's own contract lives.
- **Two surfaces, one process.** The model surface does auth, gating, and routing; the dataset surface
  serves metadata, facets, and addressing. They share the error contract, authentication, and
  configuration. The dataset surface owns no bytes and calls no backend.
- **The dataset surface is read-only and index-backed.** Metadata is loaded from the publisher's
  SQLite index at startup, and a republish is followed by a restart — that is the whole refresh
  mechanism. Facets are a deterministic projection of that index computed once, never a second store.
- **A dataset's shape is read off the index, not configured.** Which filter keys exist, whether
  geometry is RLE or boxes, whether a mask exists, and which row fields a response carries are facts
  about the published artifact, so a new dataset needs no gateway change and no dataset is
  special-cased in code. A response field is present when the index holds one — one model family with
  `response_model_exclude_unset=True`, never a model per dataset. The surface's two settings — the
  index directory and the base URL images are addressed from — are `create_app` defaults, not host
  environment; moving either is a code change.
- **One geometry encoding across both surfaces.** The model surface returns the same object shape and
  the same COCO RLE for masks as the dataset surface (`{bbox, category?, score?, rle?}`), so a
  participant writes one decoder that serves ground truth and model output alike.
- **Normalisation happens in the backend's image, never in the gateway.** Each model container turns
  that model's native output — SAM 3's raster or compressed raster among them — into that shape
  before it reaches the gateway. The gateway keeps zero ML dependencies and does no encoding.
- **A list carries statistics, never the per-image annotation.** List endpoints return counts and
  presence sets; the annotation itself is one row at a time from the detail endpoint.
- **Addresses are resolved, never concatenated.** A row is a lookup key in the closed index; a
  client-supplied URL is parsed into `(dataset, row)` and never fetched.
- **No bulk endpoints.** The largest page is 1000 rows, and there is no dataset-dump endpoint — a
  client that wants everything reads the index artifact. Batch inference stays deferred until a real
  multi-image loop appears as a need.
- **The reference is usable with a key, and the key stays on the API's origin.** The contract declares
  the scheme the gate enforces and requires it on every operation but `/health`, without which
  Swagger UI carries no credential and every Try it out answers `401`. The page arms Swagger with the
  key and asks the framework **not** to remember it (`persistAuthorization` is false): remembering is
  the shared store's job, because a browser that holds two copies of a secret cannot clear them
  together. What the field collects is typed into the API's page and never reaches the documentation
  site, which frames the page and does nothing else — which is also why only that site may frame it
  (`frame-ancestors`, configured per app), since a field for a secret in a page anything may embed is
  a field anything may overlay.
- **One key, one place: `assets/api_key.js` is the store both framed pages read and write.** The
  reference and the model page are served by this host, so they are same-origin and see one
  `localStorage` entry — a key entered on either is the key the other uses, and `Forget` on either
  clears it for both, which is why both rows offer the same two actions. Nothing else persists a key
  on this origin, and the store adopts and drops Swagger's old entry the first time it runs. Where
  storage is unavailable — private mode, a blocked or partitioned third-party frame — the key is held
  for that page's lifetime instead, and every caller already handles that: the store never throws and
  never reports which of the two happened.
- **A page that runs a call is served by the API too, and framed the same way.** `/play` is served
  from here because that makes it same-origin with the API: no CORS is involved anywhere, and the key
  it uses is the one the store holds, typed into one of the API's own pages and never into the site.
  It joins `OPEN_PATHS` and carries `frame-ancestors {DOCS_PARENT_ORIGIN}` for the reference's reason:
  it carries no data of its own, and it is where a reader's key goes. Its controls are built from the
  model's own declaration in `GET /models` and nothing else — a model the page cannot render is an
  incomplete declaration, not a page that is not clever enough — and it draws with the documentation
  site's published `post-processing/rle_decode.js`, loaded by URL, since a copy here would be a second
  decoder to keep true. It previews the image an address resolves to as soon as the address is
  complete, because a row that does not exist should say so before a model is asked to fetch it.
- **The result is shown whole, and what is drawn on it is text over the raster.** The pane holding
  the photograph has a fixed height and fits the image inside it; nothing inside a picture is ever
  scrolled. A box and its score are placed as elements in the image's own fractions rather than
  painted into the bitmap: the pane shows a photograph at whatever size fits it, so a score burned
  into the bitmap is a smudge on a tall strip and a billboard on a wide crop, while text is the same
  size in both and stays on its object. The height is in `rem` and never `vh` — this page reports its
  own height to the frame that holds it, so a viewport-relative height would be a fraction of its own
  content.
- **What the page would send is published, and the key never is.** Every value the controls describe
  is also shown as the `curl` command that sends it, built from those same controls and the page's own
  origin, so nothing about the surface is written down twice. The key is left as `$KEY`: the one thing
  a page holding a secret must not do is print it into its own DOM, where a screenshot or a command
  line pasted into a chat would carry a team's token.
- **Page material lives in `assets/`, one language to a file.** The markup, scripts, and stylesheets
  this package injects are read from beside the modules at import and inlined into the answer; nothing
  is mounted, so what the host serves is a page the application carries rather than a directory whose
  contents anything may fetch by guessing a name, and no code depends on the working directory. One
  file is one language: a page edited as JavaScript is not a string inside a Python module. The frame
  reporter and the model page are each one copy, shared by both framed pages, and the key store is
  one file inlined into both because it is one key they share.
- **The reference reads as one document.** Its field wears Swagger UI's own controls and lives in the
  strip the framework keeps for page-wide settings, between the description and the calls, rather than
  in styling of our own. Its groups come out in the order of the paths in the document, which is why
  `/health` is declared before the dataset surface: the one call that needs no key is the one a reader
  can try first. The padlock a call carries means what the reader expects of it — closed until their
  key is in, open once it is, in words as well as in the glyph — rather than what Swagger UI means by
  its own icons.
- **Liveness is a route, not a probe loop.** `/health` answers without a key so a monitor and the
  deploy script can ask it, and it says only that this process is serving, with the version — which is
  how a host running stale code is noticed. The one thing on a clock here is the pool probe above: it
  asks backends about themselves, never about this host, and nothing asks a backend on the request
  path.
- **The package configures the root logger once, on import.** uvicorn configures loggers of its own
  and never the root one, so without it every line this package writes is discarded before journalctl
  sees it — including the team id each request is attributed to, which is what stands in for a counted
  quota. `basicConfig` leaves an existing root configuration alone, so a deployment's own
  `--log-config` still wins.
- **Contract source is the Pydantic models.** Invalid input answers `400`, never FastAPI's default
  `422`; error bodies keep one shape. Every refusal goes through one helper, the framework's own `404`
  and `405` and an unhandled exception included, and a crash answers `internal_error` with nothing of
  the exception in the body — the traceback reaches the log through the server that re-raises it, so
  the handler does not log it a second time.
- **No ML dependencies.** `torch` and friends belong to the model repository; CI splits by path so
  gateway checks stay fast.
- **The token pool is a file this package owns.** `biohack-gateway tokens list|add|remove --id` is a
  subcommand of the same installed package, so the writer and the reader share one Pydantic model and
  one file format rather than two definitions of it. It edits
  `~/.config/biohack-gateway/tokens.json` — a flat `{team_id: token}` map, tokens verbatim, since
  hashing is a capability this repository does not have — and nothing else: no HTTP surface, no
  `systemctl` call, no absolute host path, so it runs on a laptop against a copy too. `list` prints
  team ids and never tokens, which is what makes it safe to paste anywhere.
- **A pool change applies without a restart, and an index change does not.** The pool's stamp —
  modification time, size, inode — is compared before every request and the file re-read when it
  differs, so issuing, rotating or revoking takes effect on the next request; revocation is the case
  that decides this, because a leaked key has to stop working without a human remembering a command.
  An edit that does not parse never displaces the last pool that did, so a typo cannot lock
  participants out, and one log line names the file. The dataset index keeps the other bargain,
  restart, because a publish is deliberate and large.
- **Stay small.** A second reader of the same index tables, or a second definition of a response body,
  is a signal to stop and reconsider: the surface is two modules on top of a shared index reader, and
  their length is mostly the required NumPy docstrings.
