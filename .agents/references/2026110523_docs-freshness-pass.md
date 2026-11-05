# The participant site caught up with the service

The site was deployed and current with `main`, but its prose still described the pre-2026-11-03
state: a ten-row prefix, two models, and no `generate` route. This pass corrected the participant
pages, the two rules that had fallen behind a code change, and the runbook's index and port lists.
It also re-recorded the quickstart, because the recorded output is the recorder's and could not be
edited by hand.

## What the live service said first

Checked with a key from the local token pool; no value is recorded here.

- `GET /datasets` returned `gwfss` 1096, `gwhd` 6512 and `gwhs-temp` 10. `GET /datasets/gwhs`
  answered `404 not_found`, which is what the `gwhs` page now says.
- `GET /models` returned 13 ids: `sam3.1-meta`, `wevisone-vl-4b`, `internvl-u`, three Nano Banana
  ids, two YOLO26 ids, two DINOv3 ids, `gwfss-segformer-b4`, `sprout-l-gwfss` and `tasselnetv4`.
- `GET /pool` reported `lab-3090`, `lab-5090`, `dgx-v100` and `vertex-global` alive. `lab-3090`'s
  row listed seven models and no `tasselnetv4`; `vertex-global` was serving all three Nano Banana
  ids. Two other hosts answered under URL keys rather than aliases, were down (`refused` and
  `timeout at connect`), and are recorded as such in `backend-pool.md`.

## What changed

- **Participant pages.** The front page, dataset index, `gwhs` page and `gwfss-segformer-b4` page
  now distinguish the two full datasets from the pending `gwhs`; the model index says nine models
  and thirteen ids and covers the generation route. `Raw HTTP` gained the `generate` route, a fifth
  curl call and a prose/picture result section; its lane sentence and the API reference now say
  that `generate` takes one of the three inferences too. The client page's upload-resize sentence
  was split, and the demo page now explains drawing `boxes` and `points`.
- **Model pages.** `yolo26` gained a `What comes back` section, `sam3.1-meta` gained a short
  `What it is not` and documents `computed_by.dtype`, and Nano Banana's `What it is and is not for`
  now precedes `Limits and cost`. The parameter tables were regenerated from the recorded
  `GET /models` and were already current.
- **Rules and runbook.** The dead campus-egress link in `gateway.md` now names
  `202610291100`; `clients.md` records the 180-second generation timeout; `models.md` names the
  four routes; `gateway.md` says a generation always takes a lane in the handler. `backend-pool.md`
  spells out the model sets instead of "all five"/"all three" and records the 2026-11-05 `/pool`
  observation. `deploy/README.md` describes the full indices, and
  `deploy/gpu-backends-local.md` now lists ports `12716`, `12719` and `12720`.
- **Quickstart.** `record_notebook.py` ran 11 cells and wrote 2 pictures; the recorded rows are now
  1096 and 6512. The answer's `computed_by.machine` changed from `lab-5090` to `lab-3090` because
  routing chose the other card that call, not because the page was edited.

## Verification

```bash
python3 docs/scripts/make_notebook.py
python3 docs/scripts/make_models.py
uv run --project docs zensical build --strict
python3 docs/scripts/sync_clients.py
```

The build reported no issues. A relative-link check over `docs/content`, `.agents/rules`,
`AGENTS.md`, `README.md` and `docs/data-layout.md` found no dead links. `git diff --check` was
clean, and the commit checks found no host address or credential-shaped literal in the diff.

## What is left

- **Nano Banana 2K and Pro are still unmeasured.** The page says so; measuring them spends the
  shared pot, so it needs a deliberate decision rather than a docs pass.
- **`gwhs` is still pending its full annotation.** The page and the dataset index say `404` until
  then.
- **The two URL-named hosts need their run lines labelled.** `/pool` shows their containers under
  `http://…` keys, so their answers cannot carry the stable alias the pool table names. That is a
  deployment fix, not a docs one.
