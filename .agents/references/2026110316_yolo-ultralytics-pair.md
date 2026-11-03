# The YOLO26 pair: two images, one folder, and what the measurements decided

Executed 2026-11-03 on `lab-3090` (`crest-nerv`), RTX 3090, driver 615.71.09. The folder is
`models/yolo_ultralytics` in the private submodule; the public side is the id pair
`yolo26-ultralytics` (boxes) and `yolo26-seg-ultralytics` (boxes and masks), one family `YOLO` in
the playground. Rules this implements: [`rules/models.md`](../rules/models.md),
[`rules/gateway.md`](../rules/gateway.md#framed-pages), and the port convention in
[`rules/backend-pool.md`](../rules/backend-pool.md).

## What is serving

| | `yolo26-ultralytics` | `yolo26-seg-ultralytics` |
|---|---|---|
| checkpoint | `yolo26s.pt`, 20,422,725 B | `yolo26s-seg.pt`, 23,467,933 B |
| release | Ultralytics assets `v8.4.0` | the same |
| host port → container port | `12717` → `12711` | `12718` → `12711` |
| declaration | `confidence_threshold`, `imgsz` (640/1024/1280, clamp) | the same |
| `MAX_PIXELS` / `MAX_DET` | 50,000,000 / 300 | 4,000,000 / 100 |
| labels | resident 0.5, per-request 0.5 GB | resident 0.5, per-request 1.5 GB |
| objects on a `gwhd` row | 0 | 0 |

Both were built here and started with `--restart unless-stopped` and the pool's
`BIOHACK_BACKEND_TOKEN`, so the gateway and the containers agree without a second credential.

## Measurements

`uv run tests/bench.py` inside the folder, `gwhd` rows 0–2, ten timed calls each with the three
warm-up calls discarded:

| `imgsz` | detect | seg |
|---|---|---|
| 640 | 6.3 ms | 7.7 ms |
| 1024 | 7.1 ms | 9.3 ms |
| 1280 | 12.1 ms | 15.1 ms |

Weights are 176 MiB (detect) and 48 MiB (seg) of device memory above the CUDA context. The mask
image's worst admitted request — 4 megapixels, 100 objects, measured on a tiled photograph — peaks
at 506 MiB and takes 357 ms, most of it the RLE encoding of a hundred image-sized masks.

Through the gateway, one `gwhd` row on an empty cache: 131 ms for detect and 119 ms for seg, which
is the LAN round trip plus the call rather than the model.

## What decided the numbers

**A mask costs `objects × pixels × 4` bytes.** Ultralytics' `retina_masks=True` returns masks at the
picture's own size, which is the only way `rle` can be decoded against the participant's frame — and
it means a 50 MP upload with 300 objects would ask for tens of gigabytes. So the mask image caps
uploads at 4 MP and answers 100 objects; the detection image keeps the 50 MP ceiling every other
container has, because a box costs nothing per pixel. The cap is a `400` that names the limit, the
same shape `gwfss-segformer-b4` uses.

**`retina_masks` is not optional here.** Without it Ultralytics returns masks on the letterboxed
input grid, padded with the bars that fit a non-square picture into a square one, and every mask
would sit a few pixels off in both axes.

**The bench had to warm twice.** The first ladder reported 640 as the slowest size — 17 ms against
7 ms — because the card had been idle and the first measured side paid the clock ramp, and because
cuDNN autotunes a shape the first time it sees it. The bench now runs one call at every side, then
thirty at the smallest, then measures. Two consecutive runs then agree to within a millisecond.

## Traps

**`import ultralytics` needs GL libraries.** Ultralytics depends on the desktop `opencv-python`
wheel, which loads `libGL.so.1` at import. The image installs `libgl1`, `libglib2.0-0` and
`libgomp1`, and the build runs an import check so a missing one is a build failure rather than a
container that exits a minute into a deploy. Without that check the failure is invisible until the
first start.

**A byte count is a truncation check, not an identity.** These weights come from Ultralytics'
release assets, not the Drive store the sibling containers read — nobody here can add to that folder.
The pin is the release tag plus the byte count, and it is a recorded deviation, not a precedent: the
next container should still prefer the store when a file id exists.

**The docs fixture needs every backend alive.** `GET /models` marks a model `declared: false` when no
backend answered, and a page generated from that says "no backend has declared this model yet". The
fixture was therefore re-recorded on `gwhs-host` with a temporary `ops-record-yolo` token, after both
containers were registered, and the token was revoked in the same session.

**The playground's model table is baked into the page.** The live `play` page kept listing two plain
ids after the presets were added, because the host serves the `play.js` of its own checkout. The
family was verified against a local gateway on `127.0.0.1:8899` with the same `models.json` and index
copied from the host; the panel then read `YOLO → v26-ultralytics / v26-seg-ultralytics` and both
calls returned `200` from `lab-3090`.

## Deployed, and not deployed

- **Backends**: running on `lab-3090`, registered in the host's `models.json` (11 ids), gateway
  restarted, `/models` reports both `declared: true` and `backends_up: 1`.
- **Docs page and fixture**: in the repository, `zensical build --strict` clean.
- **`play.js` and the host's `models.json`**: deployed. The commits were pushed, the host pulled and
  restarted, and the model list took the object form, so `play?model=yolo26-ultralytics` reads
  `YOLO → v26-ultralytics / v26-seg-ultralytics` and `GET /models` publishes the three fields for the
  eight ids that declare them. The fixture was re-recorded from that answer.

## 简化：分组从页面搬进 host 的模型清单

The picker's grouping used to live in one place — `MODEL_PRESETS` in `play.js` — which meant a new
member of an existing family appeared ungrouped until somebody edited the page. That table also named
ids, so the same model was named in `models.json`, in `play.js` and in a docs page's
`<!-- models: -->`, and the three could disagree without anything noticing.

The family, its label and its order are now fields of the host's model list, and `GET /models`
publishes them:

```json
"yolo26-ultralytics": {"urls": ["http://…:12717"], "family": "YOLO",
                       "label": "v26-ultralytics", "order": 1}
```

`play.js` reads `entry.family`, `entry.label` and `entry.order`; it keeps only what is genuinely the
page's business — which layers open first, and the example calls known to work. The docs page keeps
its `<!-- models: -->` comment, because that is the docs build's own input (which page carries which
ids) and it cannot read host configuration.

**The order of a deploy is not free.** Writing the object form into the live `models.json` while the
host still ran the previous loader made eight of eleven ids disappear from `GET /models` for about
two minutes: the old loader accepts only a list per model and skipped every object-valued entry, with
the reason in its log and nothing in the answer. It was noticed by the re-recorded directory, fixed
by restoring `models.json.bak.<stamp>` and restarting, and the code-first order is now written into
[`deploy/gpu-backends-local.md`](../../deploy/gpu-backends-local.md). The live config is back to the
list form; it took the object form after the host pulled the new loader, verified one step at a
time.

Two smaller facts worth keeping:

- **A bare list is routing only** and publishes no family fields at all — `exclude_unset` keeps the
  answer exactly what it was, rather than three nulls a reader would have to interpret.
- **`sam3.1-meta` declares a family it cannot show.** A family needs two served ids to become a
  dropdown, so its label waits for `sam3.1-wheat` to be configured beside it.

## The three example photographs

COCO's classes are not in `gwhd`, so the YOLO chips cannot use a dataset row: a chip that answers an
empty list reads as a broken page rather than as a general detector. Three photographs were chosen
for classes the checkpoints know — carrots, broccoli and a potted plant — and uploaded to `dev/` on
the data distribution through the console, because neither the publisher user nor this workstation's
operator user may write outside the dataset prefixes.

    dev/carrot.jpg        public domain, Wikimedia Commons   carrot 0.81 / 0.68 / 0.38
    dev/broccoli.jpg      CC0, Wikimedia Commons             broccoli 0.84 / 0.60
    dev/potted-plant.jpg  CC0, Wikimedia Commons             potted plant 0.79

Each was measured through the container's own upload route at `imgsz` 1024 before it was wired in.

**The page does not fetch them from the distribution.** It asks this host for
`/play/example/<name>.jpg`, and the host reads `https://data.global-wheat.com/dev/<name>.jpg` once,
keeps the bytes for the process, and answers them same-origin. Three literal paths, each in
`OPEN_PATHS` by exact string, so nothing can turn the host into a fetcher for an address of a
caller's choosing. The reason is not preference: an upload needs the bytes, and a browser can only
read another origin's bytes when that origin answers a cross-origin request.

### What went wrong on the way, in order

The CORS route was tried first and cost most of a session. Recording it so the next person does not
repeat it, and because two of the intermediate conclusions were wrong.

1. **The behaviour's path pattern was `dev/*`, without the leading slash.** CloudFront patterns must
   begin with `/`; a pattern that does not match anything leaves every `/dev/...` request on the
   default behaviour. For a while the `SimpleCORS` and `CORS-S3Origin` settings looked as if they did
   nothing, because they were never reached. The invalidation worked throughout — invalidation paths
   always start with `/`.
2. **A response-headers policy is not enough when the cache key ignores `Origin`.** The first object
   fetched without an `Origin` header was cached with no `Access-Control-Allow-Origin`, and every
   later request — browsers included — was served that entry, because the default cache policy does
   not include `Origin` in the key. The fix is a cache policy with `Origin` among its headers.
3. **The two browsers available here cannot fetch any cross-origin URL at all.** `fetch` to
   `https://api.github.com/zen`, which always answers `Access-Control-Allow-Origin: *`, fails with
   the same `NetworkError`. Every browser-side verdict in this session was therefore untrustworthy,
   and the "still broken" conclusions drawn from them were not evidence about the configuration.
   A `curl` with the browser's exact headers answered `access-control-allow-origin: *` throughout.

The `dev/*` behaviour is left in place — it is correct now, and harmless. Nothing depends on it.
