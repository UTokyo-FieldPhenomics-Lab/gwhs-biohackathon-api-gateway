# What resizing on the participant's machine costs

`resize_before_submit=True` moves work from the Pacific link onto the caller's CPU. The question this
answers is whether that work is a moment or a stall: a client that spends seconds before it sends
anything has made the API slower, not faster.

Not published: these numbers describe one machine, and the docs should not promise a latency a
participant's laptop may not meet.

## Where

`labpc` — the machine that is also `lab-3090`, an AMD Ryzen 9 5950X (16 cores, 32 threads) with
125 GiB of RAM. Same machine, same session, three languages.

## Method

Each client's tests generate a 4000×3000 JPEG and a 4000×3000 PNG at run time (never committed), warm
up once, then time the whole preparation — open, EXIF transpose, resize, encode — and take the median
of five. The pass-through case is an image already at the model's side, which must cost nothing
because it must not be re-encoded at all. Each language writes its own fixture, so its file size
differs; the pixel count is the same.

| language | 12 MP JPEG | 12 MP PNG | pass-through | output |
|---|---|---|---|---|
| Python (Pillow, `Image.draft`) | 46 ms | 204 ms | 0.02 ms | ~37 KB |
| Java (`ImageIO`, bicubic) | 66 ms | 92 ms | 0.15 ms | ~36 KB |
| JavaScript (`sharp`) | 84 ms | 30–35 ms | 0.2–0.3 ms | ~322 KB |

The ceilings the tests assert are 400 ms (Python), 1500 ms (Java PNG; 800 ms JPEG) and 200/300 ms
(JS): they catch a client that takes seconds, not a twenty per cent regression.

Two readings that look odd and are not:

- **Python is fastest on JPEG and slowest on PNG.** `Image.draft` lets libjpeg decode a JPEG directly
  at 1/4 or 1/8 scale, so a 12 MP photo is decoded at about 1 MP; PNG has no such shortcut, and the
  full 12 MP is decoded before the resize.
- **`sharp` is faster on the PNG than on the JPEG.** libvips decodes PNG quickly and the JPEG path
  pays a full decode plus its own resize; the PNG fixture is also a gradient, which compresses to a
  fraction of the file.

A prototype measured with a 10 MB noise JPEG (a real phone photo is closer to that than to a
gradient) took 114 ms in Pillow — still a quarter of the ceiling.

## Does the resize change the answer?

`clients/python/tests/test_live.py::test_resizing_before_upload_does_not_change_the_answer`, run
against the live API on 2026-10-20:

- row 0 of `gwhs-temp` (592×2509), upscaled to 708×3000 so the original really is larger than the
  model's side, 452 KB;
- sent once at 708×3000 (`resize_before_submit=False`) and once at 238×1008 (the model's 1008);
- one object from each; scores 0.512 against 0.551; normalised boxes
  `[0.2783, 0.0899, 0.4304, 0.5822]` against `[0.2844, 0.0919, 0.4216, 0.5811]`; box IoU **0.975**.

The test asserts an object count within one, a box IoU of at least 0.85, and a total area within
10 %. It skips when no published row holds something the model recognises, which is what the dev
rows mostly are.

## The cache hit, for scale

Measured through uvicorn on loopback with `httpx`, 21 calls to the same address: 20 answered from the
cache, one reached a backend. Cache-hit p50 **4.74 ms** (min 4.34, max 7.88), against 675 ms for a
row prediction through the pool. The handler itself spends 0.1 ms; the rest is the request.
