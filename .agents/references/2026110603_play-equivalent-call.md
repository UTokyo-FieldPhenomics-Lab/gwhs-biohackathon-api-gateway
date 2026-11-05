# The play page's equivalent call

The panel under the controls is generated code: the request the page would send, written as a Python
call, a JavaScript call, a Java call and a curl command. A reader copies one. This pass checked that
each is the page's own request — same names, same values — and that each runs.

## How it was checked

The live page was driven in Firefox with a participant token. Controls were set through the DOM the
way a reader sets them (a model, a dataset and row, an upload, a slider, a select, a geometry list, a
family checkpoint, an example chip), and the four panels were read from the page. Ten states, covering
every shape the declarations have today: no parameters, a bounded number, an enumerated number, an
enumerated string, a single-value segmented enum, a long text with quotes, a backslash, an apostrophe,
a newline and an em dash, one and two geometry lists, a row and an upload, resize on and off, and a
filename with a space, an apostrophe and a `#`.

For each state:

- the Python, JavaScript and Java snippets were compiled with `compile()`, `node --check` and
  `javac` (wrapped in a class with the client);
- the Python snippet was evaluated against the live API with the real client, the JavaScript snippet
  with the Node client, and the Java snippet compiled once and run — all ten answered;
- the curl command was run verbatim in `bash` with `$KEY` exported, including an upload whose file
  name carries a `;`, a `"` and a backslash;
- the note that explains the JavaScript tab's `file` was checked in a local copy of the page with the
  working-tree assets, since the live page serves the deployed one: it appears for the JavaScript tab
  and an upload and is hidden for a row, the Python tab and the HTTP tab;
- both example-chip paths were exercised: `tasselnetv4`'s "3 exemplars" chip wrote three boxes and
  its generated Python call answered 41.4 on `gwhd` row 4, and `yolo26-ultralytics`'s photograph chip
  loaded `carrot.jpg` into the file control and its Python and curl both answered three carrots;
- the client call's arguments were compared with the page's own HTTP query, parameter by parameter.
  Ten of ten matched; the only difference was the client-only `resize_before_submit` /
  `resizeBeforeSubmit` switch, which is the client's and is never forwarded.

That is 10 states × 4 languages = 40 snippets, of which the 30 client snippets and 10 curl commands
all answered with a real model.

## What was wrong

### 1. A `;` in the uploaded file's name broke the curl command (fixed)

`curlText` quoted the whole form value for the shell, but curl parses a form value again for its own
options: `-F 'file=@a;b.jpg'` is the file `a` with an option `b.jpg`. Curl exits 26 without sending
anything. Only the HTTP tab was affected — Python, Java and the JavaScript client take the name
untouched — but it is the tab a reader with a shell reaches for.

`play.js` now builds the part with `formFile`, which double-quotes the path inside the value and
escapes a `"` or a `\` in it: `-F 'file=@"a;b.jpg"'`. Verified against curl with `a;b.jpg`,
`my photo.jpg`, `a"b.jpg`, `a\b.jpg` and `photo.jpg`; all five upload and answer. The change is
committed, and the live page keeps the old line until the gateway is redeployed.

### 2. The JavaScript upload snippet names a variable nothing defines

Every other language names the file as a path — `"photo.jpg"`, `new java.io.File("photo.jpg")`,
`file=@photo.jpg` — but the JavaScript tab reads

```js
client.predict_file("sam3.1-meta", file, {prompt: "wheat head"});
```

`file` is a free variable. In a Node script it is `ReferenceError: file is not defined`. In a console
on the page itself it resolves to the element with `id="file"` — the `<input type=file>` — and the
client then uploads the string `[object HTMLInputElement]` as bytes, which the API refuses. The docs'
JavaScript page uses the same placeholder ("use `predict_file(..., file, ...)`"), so the page and the
guide agree and disagree with the runnable case at the same time.

The JavaScript client takes bytes, not a path, so a one-line snippet has to name *something*. The
page keeps the placeholder, and now says what it is instead of leaving the reader to find out: a note
under the panels, shown only for the JavaScript tab on an upload, reads "`file` is a `File` or `Blob`:
a browser gets one from an `<input type="file">`, and a Node script from
`const file = await readFile("photo.jpg")`, imported from `node:fs/promises`". The JavaScript guide
defines it at the same place its first upload example appears. Verified in a local copy of the page
with the working-tree assets: the note appears for the JavaScript tab and an upload, and is hidden for
a row, for the Python tab and for the HTTP tab.

The snippet itself is unchanged; it is still a call that assumes the reader has the bytes. The
alternative — emitting `const file = await readFile(...)` above the call — would be runnable in Node
and wrong in a browser, and the page is a browser page.

## Smaller observations

- **A required parameter is not marked.** `nano-banana-2` declares `prompt` required and no default;
  with nothing typed the page omits it and shows a call that the API refuses with `400 invalid_request`.
  The page's Run would be refused the same way, so the panel is truthful — but a reader copying it
  learns the refusal rather than the requirement.
- **The JavaScript row snippet returns a pending Promise.** `client.predict(...)` with no `await` or
  assignment prints `Promise { <pending> }` in a console; the JavaScript guide writes
  `const answer = await client.predict(...)`. Python and Java show the answer's shape.
- **A model with no declaration still gets a request panel.** Selecting `internvl-u` while its
  container is down empties the parameter controls and says "no backend is answering, so nothing can
  be run", yet the panel still shows a call to that id with no parameters (and Run is disabled).
- **The page uploads at 1008 px, the client snippets at the model's side.** The comment above the
  curl says so, and every answer is the same because the container resizes again. It is a difference
  in bytes and time, not in the call's meaning.
- **Parameter names are interpolated as identifiers.** Python writes `name=value` and JavaScript
  writes `name: value` without quoting. Every id served today declares `prompt`, `boxes`, `points`,
  `confidence_threshold`, `imgsz`, `side`, `aspect_ratio`, `image_size`, `max_instances`, `mode` and
  `size`, all identifiers. A declaration with `class` or `max-instances` would make the Python and
  JavaScript snippets invalid while the Java one (quoted keys) stayed valid. The page is built to know
  no parameter name, so this is a container-side naming constraint that is not written down.

## Commands

The browser harness, the snippet fixtures and the three runners are in `/tmp/opencode/probe/`
(`run_snippets.py`, `run_js.mjs`, `compare_snippets.py`, `snippets.json`); they are a test run, not a
maintenance surface. `gateway/tests` still passes after the `formFile` change.
