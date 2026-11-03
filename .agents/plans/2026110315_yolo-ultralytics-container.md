# The YOLO26 containers, then the picker

Agreed 2026-11-03. Two ids from one folder — `yolo26-ultralytics` (detect) and
`yolo26-seg-ultralytics` (instance segmentation) — because the official checkpoints differ only in
weights and in whether an answer carries `rle`. A fine-tune lands later as `yolo11-gwhd` /
`yolo11-gwfss` in a folder of its own (`yolo_wheat/`), so their dependency lock cannot move this
one. The family label in the playground is `YOLO`; the ids stay separate because a request names an
id and nothing else.

## Steps

- [x] Copy the compatibility layer into `models/yolo_ultralytics/`: `outputs.py` (COCO RLE, boxes,
      `computed_by`), `infer.py` (ultralytics `YOLO`), `serve.py` (the three routes, `imgsz` clamp
      and `confidence_threshold`), `tests/outputs.py`, `tests/bench.py`, `tests/labels.py`,
      `fetch_weights.py`, `Dockerfile`, `README.md`, lock.
- [x] `tests/outputs.py` green without weights or a GPU, decoding through
      `clients/python/global_wheat.py`.
- [x] Build both images on this 3090, run the bench, and record resident/per-request VRAM and
      latency at 640 / 1024 / 1280; set the labels from the measurement, not from a guess.
- [x] Run both containers, check `/health`, one dataset row and one upload through each.
- [x] Register both ids in the host's `models.json` and restart the gateway; `/models` reports
      `declared: true`, `backends_up: 1` for each.
- [x] UI, repository side: `play.js` presets for both ids (`family: "YOLO"`), the docs model page
      with one `parameters:<id>` block per id and the AGPL-3.0 notice, `docs/scripts/fixtures/`
      re-recorded from the live API, parameter tables regenerated.
- [ ] UI, live side: the host serves `play.js` from `origin/main`, and this checkout is three
      commits ahead of it. The presets are verified against a local gateway; the live page shows
      the two ids ungrouped until those commits are pushed and the host pulls.
- [x] Record the ports in `.agents/rules/backend-pool.md` and `deploy/gpu-backends-local.md`;
      write the reference note.

## Decisions already taken

- `imgsz` is declared as measured `values` with `above_maximum: clamp`, like `sam3.1-meta`.
- `confidence_threshold` keeps the name and bounds `sam3.1-meta` publishes.
- The seg image caps uploads at 4 MP and answers at most 100 objects, because `retina_masks` costs
  `objects × pixels × 4` bytes; the detection image keeps the 50 MP ceiling.
- Weights come from Ultralytics' immutable `v8.4.0` release assets with an asserted byte count —
  not the Drive store, which nobody here can add to. Recorded as a deviation in the folder README.
- `models/yolo_wheat/` is not created until a fine-tune exists; the folder is per model line, and
  an empty one would be a build nobody can run.
