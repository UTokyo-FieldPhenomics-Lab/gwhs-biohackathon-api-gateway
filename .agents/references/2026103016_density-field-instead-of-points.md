# The counting answer publishes a field, not points

What was built on 2026-10-30 under [`plans/2026103015_✅density-field-instead-of-points.md`](../plans/2026103015_✅density-field-instead-of-points.md),
what the measurements were, and the two things a later session would otherwise redo. The decisions
themselves are in the plan; this file is the record.

## The shape that shipped

A counting answer carries `count` and `density` (`{size: [rows, columns], values: [..]}`), `objects`
stays an empty list, and **`points` stays in the contract** as the point-counting model's field. The
container stopped sending it; `ModelPrediction` still declares it, the page still renders it, and a
P2PNet-style container would need no gateway or page change at all.

`count` is now the sum of the **cropped** field, not of the raw tile output. The model puts mass on
the zero padding; on a two-tile frame that was 6% of the total (yicib `image10.jpg`: 55.24 raw,
50.30 cropped). The answer and the field therefore describe the same rectangle.

## Measured: the grid is not 24×24 and is not fixed

The attention map beside the density head is always 24×24, but the density head's own grid is
**23, 21 or 17** cells per 384-pixel tile, chosen by the level the exemplar's resized size selects:

| exemplar, resized px | level | grid per tile | cell | on a 1024-pixel-tall photo |
|---|---|---|---|---|
| < 32 | 32 | 23 | 16.7 px | ~45 px |
| < 64 | 64 | 21 | 18.3 px | ~49 px |
| < 128 | 128 | 17 | 22.6 px | ~60 px |

Measured in the container on `yicib/image10.jpg` with one square exemplar per row. So `size` has to
travel with the values, and `infer._crop_padding` reads the cell off `grid.shape[0]` instead of
`TILE // 24` — the first draft used the attention cell and cropped a 42-column grid as if it were 48,
keeping two padding columns and dropping two picture columns.

The field is spatially aligned: four bright squares in the four corners of a 384×384 probe return
density at the four corners of the grid, with the exemplar's own corner strongest.

## Measured: what the field looks like on real pictures

- `gwfss` row 0, three 31×36 exemplar boxes: one cell holds 1.92 and 1.28, the rest of the 529 cells
  sit between 0.05 and 0.2. Scaling the page's alpha by that maximum showed a single blob; scaling by
  a fixed "one object per cell" showed almost nothing. The page uses the field's **ninth decile** as
  full tint, saturating above it, at alpha 150/255 — that shows the base and the peak on the same
  layer.
- `yicib/image10.jpg` (a uniform pile of nuts, 48 annotated): the field concentrates into a vertical
  band of five columns rather than following the pile, while the count is 50.3 against 56. The
  model's **spatial** output is much weaker than its count; say so if a participant reads the
  heatmap as a detector.

## The point renderer that was kept on purpose

Decided in conversation: `points` stays, because a point-set model is a real candidate and rebuilding
the shape and the renderer would be the waste. Where it lives:

- `gateway/src/biohack_gateway/models.py` — `ModelPrediction.points`, `[x, y, score]` in the image's
  pixels; `score` is the model's own score, deliberately not defined by this project.
- `gateway/src/biohack_gateway/assets/play.js` — the loop in `render()` that appends a `.point` div
  per point through `place()`, plus `.point` in `play.css`. It reads `last.points`, which `paint()`
  fills from `answer.points`; an answer without points leaves the picture unchanged.

What was **deleted** and must not be restored for a density model: `infer.points_from_attention`,
which took the `round(count)` highest attention cells and called them points. It is what made an
over-count of fifteen look like fifteen detections scattered over empty ground. A point model emits
its own points from its own head; the renderer and the wire shape are the reusable half. If the code
itself is ever wanted as a reference, it is in this submodule's history at `f0ef510`.

## The display row after the second round

Two more changes were decided after seeing the heatmap on a real photograph:

- **`image` is the fifth eye and the first button**, because it is what every other layer is drawn
  over. With it off and `masks` on, the layer is painted over a dark ground (`#12161c`); over the
  pane's light grey a pale tint read as nothing. With both off the pane is empty, which is honest:
  both layers have been turned off on purpose.
- **`boxes` now hides every rectangle on the picture** — the exemplars the reader drew and the boxes
  a model answered — so the toggle has a job for a counting model. Hiding it locks the picture
  (`boxesEditable = false`), and asking to edit turns the layer back on: a drag into a hidden layer
  would draw a box nobody can see. The same invariant is applied when a model's view defaults hide
  boxes.

## Deployed 2026-10-30

Submodule `8072ffc`, parent `ae2a3c0`; the gateway host runs the second.

- **`homepc-4090` and `lab-5090` rebuilt from a source archive**, not from a git checkout: `git archive
  -o build/tasselnetv4-<sha>.zip HEAD:tasselnetv4` in `models/`, `scp`, then `docker build` on the
  machine — the weights layer is cached there, so only the code layers rebuilt. The WSL distro on the
  5090 has no `unzip`; `python3 -m zipfile -e <zip> <dir>` did it. Both were restarted by reading
  `BACKEND_TOKEN` and `TNV4_MACHINE` out of the old container's own environment, so the run line keeps
  the values it had rather than a value copied from somewhere else.
- **`lab-3090` is still the CPU container** (`crest-nerv-cpu`): the driver mismatch above is unchanged,
  so this is the one row of the plan's S5 not done. It answers the public API at about 0.25 s, slower
  than a GPU but not a demo problem; it needs a reboot to become a 3090 backend again.
- **The host pulled `--ff-only`, was restarted, and its `cache.sqlite*` was deleted** — an answer
  stored before this change has no `density`, and the store is disposable by design.
- **Public check** (`POST /models/tasselnetv4/predict?boxes=…` on `gwfss` row 0): `count` 6.94,
  `density.size` `[23, 23]`, 529 cells summing to 7.03 (three-decimal rounding over 529 cells), no
  `points`, `computed_by.machine = homepc-4090`. Five calls over rows 2–6 on `homepc-4090` answered in
  0.30 s each.

Two things a later session should know:

- **`docs/scripts/fixtures/model-directory.json` now records `crest-nerv-cpu`** in `tasselnetv4`'s
  machine list, because that is what the live API answered. Nothing renders the machine list — the
  generated tables read `parameters` only — but re-record the fixture once `lab-3090` is back on its
  card, so the tracked file names the machine the pool's inventory names.
- **`make_models.py`'s docstring advertises `--check`, and the parser has no such flag.** Pre-existing,
  found while refreshing: the script writes and reports "already current" per page, which is the same
  information.

## The workstation's container is GPU-configured but not running (2026-10-30 18:4x)

Told to put `lab-3090` back on the card even though the machine was about to lose power, the CPU
container was removed and the GPU run line created in its place:

    docker run -d --gpus all --restart unless-stopped -p 12713:12711 \
      -e BACKEND_TOKEN=… -e TNV4_MACHINE=lab-3090 --name tasselnetv4 tasselnetv4

It sits in **`Created`**, because the prestart hook still fails (`nvml error: driver/library version
mismatch`), and with no process listening on `12713` the pool serves `tasselnetv4` from two machines.
The fake `nvidia-smi` mount is gone with the old container, and `TNV4_DEVICE` is unset, so the
container's own default applies.

**The reboot clears it, and this is why:** the running kernel is `6.18.49-1-MANJARO`, whose module
directory no longer exists, and the 610.57.04 module is only in memory. `/lib/modules` holds
`6.18.55-1-MANJARO`, whose `extramodules/nvidia.ko.zst` is **615.71.09** — the same version as
`nvidia-utils`. After the power-cycle boot, kernel, module and userspace agree and `--gpus all` works.

Whether Docker starts a container that has never run is not something this session verified. After
the boot, check `docker ps --filter name=tasselnetv4`; one `docker start tasselnetv4` settles it, and
`computed_by.machine` should read `lab-3090` (not `crest-nerv-cpu`) in the next public answer.

## Failure record

- **A canvas showing a cross-origin picture cannot be read back.** `getImageData` on `#canvas` threw
  `SecurityError` because the row's image comes from `data.global-wheat.com` without CORS. The
  heatmap was verified by screenshot, not by counting pixels.
- **The gateway reads `/play` at import.** Every asset edit needed the demo process restarted; a page
  that looks unchanged is a stale process, not a stale browser.
- **A cache hit hid a new shape.** The second run of the same boxes from the same row returned the
  answer stored before the crop fix. Change the boxes, or the query text, to force a miss.
- **This workstation's NVIDIA userspace moved under the running driver**: the kernel module is
  610.57.04 and `libnvidia-ml` is 615.71, so `docker run --gpus all` fails with `nvml error:
  driver/library version mismatch` until the machine reboots. The local demo ran on CPU meanwhile,
  with a stub `nvidia-smi` bind-mounted at `/usr/local/bin/nvidia-smi` (it exits 0 with no output, so
  `check_budget` reports "the driver did not report a card" and starts). The container was labelled
  `crest-nerv-cpu` for that reason; the public API's `lab-3090` row answered from it until reboot.
- **The vendored model prints every parameter name in `__init__`**
  (`vendor/models/local_count_multihead/...:98`), so a container's log opens with ~300 lines of key
  names. Vendor, unchanged, not ours.

## Still open

- Deploying the rebuilt image to `homepc-4090`, `lab-5090` and `gwhs-host` (S5 of the plan), and
  restoring this workstation's GPU container after a reboot.
- The docs model page for `tasselnetv4`, and PAC-Somalia presets on `/play` (`yicib` 56→54.9,
  `talal` 42→45.3, `mir_qalooc` 69→68.9 are the good ones; `salaamuko` over-counts by about 15).
