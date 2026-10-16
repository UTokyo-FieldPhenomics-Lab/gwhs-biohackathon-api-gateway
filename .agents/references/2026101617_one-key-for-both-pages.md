# One key for both pages, and the two things that only a browser showed

Session of 2026-10-16, following [`2026101617_the-model-page-and-the-play-page.md`](2026101617_the-model-page-and-the-play-page.md).
The question the user asked was why the model page asked for a key the reference page had already been
given. The answer was that nobody had asked; the recorded constraints only ever said the model page
would not *write* one. The user asked for the shared state, and this is what it took.

## The measurement that decided it

Two pages of the API origin, one framed by a page of another origin (the documentation site's
arrangement, modelled with a throwaway parent on a second port and a probe served on the API origin):

| what was measured | result |
|---|---|
| frame sets, another frame of the same origin reads | `setter=alpha reader=alpha` — shared |
| a top-level visit to the API origin sets, a frame reads | the frame saw the value — shared here too |

The first line is the one the design rests on, and it holds in every browser that partitions
third-party storage: the partition key is the top-level site plus the frame's own origin, and both
frames have the same pair. The second line is Chromium-specific — Safari and Firefox partition a
framed page's storage away from a top-level visit — which is why the store degrades to a page-lifetime
value rather than promising more than it can keep.

## What was built

- `assets/api_key.js` — the one store, inlined into both framed pages. `get` / `set` / `clear` /
  `onChange`, one entry (`biohack-gateway/api-key`), and no throwing: a browser in private mode, a
  blocked frame, a partition of its own — each leaves the key held for that page's lifetime, which is
  what the page did before there was a store. It also adopts Swagger's old entry once and drops it, so
  nobody who had already pasted a key has to paste it again, and the origin stops holding two copies.
- `app.py` now asks Swagger **not** to persist (`persistAuthorization: false`), because two remembered
  copies of a secret cannot be cleared together, and the copy Swagger owned was one the model page
  could not clear.
- Both rows offer the same two actions: `Use key` and `Forget`. The model page sets the store before
  announcing, so its own watcher stays quiet, and its `Forget` also resets every control that was
  filled in with the key that has gone.
- `storage` events make it live between two tabs: measured both ways — a key set on the reference
  cleared the model page's state, and a key set on the reference connected a model page that was
  already open, with no reload.

## Two bugs that only a browser showed

- **`for (const id of ("a", "b", "c"))`** — the comma operator, so the loop iterated the *characters*
  of `"check"` and `getElementById("c")` returned null: an uncaught `TypeError` halfway through the
  model page's reset. It had been in the file since the first version of that handler, unexercised,
  because until this session the page had no Forget button. An array literal is the fix; the lesson is
  that a loop over a parenthesised list is a loop over a string.
- **`preauthorizeApiKey` does nothing before the spec arrives.** Swagger fetches `/openapi.json` after
  our script runs, and until its security definitions exist that call is a silent no-op — so arming
  once at load meant the remembered key was applied to nothing, and the page reported "no key yet" on
  every fresh visit with a key sitting in the store. The key is now applied from `settle`, which the
  MutationObserver runs whenever the container changes, and only while Swagger holds none. Measured
  after the fix: a fresh load reports `{"ApiKeyAuth":{"value":"test-token-real…","schema":{…}}}` armed
  and "a key is in use — paste another to replace it".

A third, smaller one: the throwaway proxy raised on any non-2xx, so a `401` from the gateway closed the
socket and the page reported `Failed to fetch` instead of the refusal. Hardened for the check only.

## The interface, as asked for

- The image choice is one dropdown (`a published row` / `your own photograph`), and the controls under
  it change with it.
- **The image is previewed as soon as the address is complete** — on connect, on a dataset change, and
  on the row number (debounced, because typing `12` would otherwise fetch rows 1 and 12). A preview in
  flight is dropped when Run is pressed, by a token, so a stale preview cannot draw over a result. A
  row that does not exist says so in the caption (`no such row`) instead of failing at Run.
- **Run moved to the end of the image row**, which removed a row from the top of the page. Measured
  after the move: the button sits at the right edge of that row, on the same line as the controls that
  choose what it runs on.

## What is still open

The same three as the plan: the recorded call for the demo panel, the rehearsal against a real GPU
backend, and the second-model invariant check. One new one belongs to this session: the shared store
has been measured against a local rig, not against the deployed pages, so the first live check is a
key pasted on `docs.global-wheat.com/interactive` being used by the Models page's frame without being
typed again.
