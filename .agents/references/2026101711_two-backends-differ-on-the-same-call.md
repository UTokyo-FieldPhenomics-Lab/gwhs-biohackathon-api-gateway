# Two backends of one model id did not answer one call identically

Status: **open**, recorded 2026-10-17. The cause is one of two candidates and the check that separates
them is at the end. What is certain: the same model id, the same image, the same prompt and the same
threshold answered with a different number of objects and a different total mask area, depending on
which backend took the call.

## What happened

The documentation page's recorded example is `gwfss` row 4, prompt `wheat head`,
`confidence_threshold` 0.25 — the row and parameters tuned by hand on the API's own `/play` page. Asking
labpc's container (3090, image `sam3.1-meta:latest`, built 2026-10-17 10:36 JST) the *same* call:

| Asked where | Objects | Σ decoded mask = Σ area |
|---|---|---|
| `/play`, against the deployment's two backends | 17 | 102,002 px |
| labpc's 3090 container | **18** | **109,419 px** |

Both answers are internally consistent: in both, every object's decoded pixel count equals its
published `area` exactly, and the sums agree with each other. Only the two machines disagree.

## What is ruled out

- **Not the image.** Both calls fetched `gwfss/images/00000004.png` (512×512, 508,812 bytes). Images
  under `data.*` are immutable and the gateway resolves `(dataset, row)` through its own index, so
  neither side can have addressed a different object.
- **Not stale code.** The image in question was created 49 minutes before the run, and the `serve.py`
  inside it is byte-identical to the working tree:
  `60f1506c374ca3e31895ad728370565e737c8da19a9bb83e3a069772b2f10ac0`.
- **Not the threshold, and not only a count.** On the 3090 the answer is 18 objects for every
  threshold from 0.25 to 0.30, and 17 from 0.31 — but those 17 sum to **100,935 px**, not the
  102,002 px the other machine reported. So it is not one object crossing a score's edge: the masks
  themselves differ, by about 1% of their area, across the objects both machines did find.
- **Not nondeterminism run to run.** Repeating the call on the 3090 returns the same 18 objects and the
  same 109,419 px every time.

## The two candidates

1. **Two different cards.** A 5090 and a 3090 choose different cuBLAS/cuDNN kernels and the forward
   pass runs in bfloat16, so mask boundaries move by a pixel and the proposal set can change at the
   margin — enough for a different object count and roughly a percent of mask area. This would be
   ordinary numerics and needs no fix, only a documented expectation: an answer is one machine's
   answer.
2. **Two different inputs to the model.** A different checkpoint, a different image preprocessing, or
   a different torch/cuda build between the two images would produce exactly this shape of difference
   and *is* a defect — the pool's premise is that any backend of an id is interchangeable.

## What settles it

- Ask **each** backend directly for the same call — `docker exec` on one and the tailnet address of the
  other — and compare the per-object `score`, `area` and `bbox` lists. Candidate 1 leaves scores within
  a few thousandths and boxes within a pixel; candidate 2 does not.
- Compare the checkpoint each container actually loaded: its size and hash, read inside each
  container, not from the build host.
- Compare `torch.__version__` and `torch.version.cuda` inside both images, since the pipeline pins them
  in `pyproject.toml` but two builds can resolve differently if the lock is not honoured.

Until one of them is excluded, the documentation page carries the weaker, still-true statement — a
mask's boundary, and the pixel count that follows from it, is one card's figure — and the recorded
example is the 3090's answer, stored whole so it can be checked rather than trusted.

## Update, 2026-10-17

The user confirmed the **model did not change** between the two answers, which rules candidate 2 out:
what differs is the machine and the build, not the weights. Candidate 1 stands, and an answer now
carries its own evidence — every response has a `computed_by` record of the machine's name, its
card, the card's capability, and the CUDA and torch versions — so the next time two answers disagree, the difference is
one field away instead of a comparison exercise.

The documentation page's recorded example is the 3090's answer to `gwfss` row 4 at 0.25: 18 objects,
109,419 px, with that record in it.
