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

## Resolved: two cards, one stack

Measured after both backends were rebuilt to carry a `computed_by` record, which answers in one field
what this note could only ask before — same image, same prompt, same `confidence_threshold` 0.25:

| Backend | `compute_capability` | `cuda` | `torch` | `gwfss` row 4 |
|---|---|---|---|---|
| labpc, RTX 3090 | **`8.6`** (Ampere) | 12.8 | 2.10.0+cu128 | **18 objects, 109,419 px** |
| lab 5090 | **`12.0`** (Blackwell) | 12.8 | 2.10.0+cu128 | **17 objects, 102,002 px** |

The card, its capability, CUDA and torch are the only fields either backend reports, and one of them
differs: the kernel family the runtime selects. That is candidate 1 and it is ordinary numerics, not a
defect — the checkpoint, the code and the pinned stack are the same on both machines.

What it costs is a promise, not correctness: **an answer is one machine's answer**, so a documentation
page that published "this call finds seventeen heads" would be false on the other card. The page states
the mechanism instead of a count, and a participant comparing two calls should expect a pixel, a
percent of an area, or an object of difference when the pool has mixed cards.

If a count ever has to be stable across the pool, the fix is a homogeneous pool — the same card and the
same build — not a code change. Mixed cards are the normal state here, and now the answer says which
one it came from.

## Update, 2026-10-17

The user confirmed the **model did not change** between the two answers, which rules candidate 2 out:
what differs is the machine and the build, not the weights. Candidate 1 stands, and an answer now
carries its own evidence — every response has a `computed_by` record of the machine's name, its
card, the card's capability, and the CUDA and torch versions — so the next time two answers disagree, the difference is
one field away instead of a comparison exercise.

The documentation page's recorded example is the 3090's answer to `gwfss` row 4 at 0.25: 18 objects,
109,419 px, with that record in it.
