# What an upload can be

Asked on 2026-10-17: restrict uploads to images, and make sure no parameter can carry something
executable. Half of that was already true by construction, and finding out which half is the useful
part of this note.

## What the code already did

- **Nothing is executed and nothing is written under a name a client chose.** `file.filename` appears
  nowhere in the gateway; the bytes are read into memory and forwarded to a backend, which only turns
  them into pixels. There is no shell, no `eval`, no `exec` on any path from a request.
- **Parameters are typed by Pydantic** before any handler runs: `row` is an `int`, `dataset` a `str`
  looked up in an in-memory dictionary, `confidence_threshold` a float bounded to 0–1. A wrong type is
  a `400` from the validation handler, not a string that reaches a downstream interpreter.
- **The index SQL is static.** `dataset_index.py` holds nine `SELECT` statements with no `{}` or `%`
  in them; they are executed once at startup to fill memory. A dataset id never becomes SQL.

So "an executable upload" had no route to run. What it did have was two costs and one unbounded field:

- a `.php`, an archive or a text file still reached a backend, which refused it after the request had
  taken one of three lanes;
- a small PNG can declare a hundred million pixels, and `convert("RGB")` allocates three bytes for
  each of them on a machine that also runs a desktop;
- `prompt` had no length bound and is forwarded verbatim into a backend's query string.

## What changed

- **The gateway decides the type from the bytes**, against a JPEG/PNG/WebP/TIFF/GIF table, and
  forwards *that* media type rather than the multipart `Content-Type` the client claimed. Anything
  else answers `400` before a backend is asked, with a message naming what is accepted and telling a
  HEIC or RAW sender to convert to JPEG first.
- **The container refuses an image above 50 megapixels** before `convert`, and sets
  `Image.MAX_IMAGE_PIXELS` to the same number so a header above twice it is refused while it is being
  read. Pillow's own guard warns above the limit and raises only above twice it, so the explicit
  comparison is what makes 50 the number it says.
- **`prompt` is bounded at 200 characters** on both predict routes.

Fifty megapixels is a memory bound, not a quality one: every input is resized to 1008², so this is
fifty times what a call can use and still accepts a 48 MP phone photograph or a 50 MP medium-format
file. At three bytes per pixel the worst admitted request now allocates 150 MB.

## Verified against the deployed host

| Sent | Answer |
|---|---|
| `<?php system($_GET['c']); ?>` declared `image/jpeg` | `400` — "not an image this API accepts; send JPEG, PNG…" |
| a **45-byte** PNG header declaring 10000×6000 | `400` — "the image is 10000x6000 pixels; the limit is 50 megapixels" |
| a 400-character `prompt` | `400` |
| a real 2048² JPEG | `200` |

`GET /models` still reads `backends_up 3`, a prediction still answers `200`, and the gateway logged no
backend failure during the run, so neither refusal cost a lane or a health mark.

## Still open, and deliberately not fixed

- **`/tmp` on the gateway.** Starlette spools an upload above 1 MB to a temporary file and the gateway
  deletes it when the request ends; the file is never named by the client and never executed, but the
  host has 1936 MiB of RAM and 15 MB × concurrent uploads is real page-cache pressure. The 15 MB edge
  limit is what bounds it.
- **No content scanner.** An image that is also a valid polyglot is still an image to a decoder, and
  the decoder is not the exploit surface — it produces pixels. A malware scanner would be a second
  authority over bytes nobody executes.
- **HEIC and camera RAW are refused**, because Pillow cannot decode them. Supporting them later means
  a `pillow-heif` dependency in the container image, not a change in the gateway.

Deployed: host `d611437` (gateway code), images `6d2cd81` on `lab-5090`, `lab-3090` and
`homepc-4090`.
