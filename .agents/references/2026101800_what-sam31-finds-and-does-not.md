# What SAM 3.1 finds, and what it does not

Moved off `models/sam3.1-meta.md` on 18 October 2026. The page states the contract; the measurement
belongs here, where it can be re-run and where a card's timing does not read as a promise.

## The measurements

Prompted with `wheat head`, on the published rows, at 1008, one answer per row:

| image | result |
|---|---|
| `gwhs-temp` row 0 — 592×2509, one spike on dark ground | **1 object**, score 0.520, `area` 173,777, box 254×1461 — the whole head |
| `gwhs-temp` row 1 | 1 object, score 0.715 |
| `gwhs-temp` rows 2–3 | 0 objects |
| `gwhd` row 0 — 1024×1024 field quadrat, **144 annotated heads** | **0 objects**, at every threshold from 0.5 down to 0.05 |
| `gwfss` row 0 | 0 objects — and correct: that row's own palette holds no head pixels |
| `gwfss` row 4 — 512×512, cut heads lying on straw | **18 objects** at `confidence_threshold` 0.25, `Σ mask` = `Σ area` = 109,419 px |

## What it means

At 1008 this model segments **a conspicuous head in a close-up** and finds **none of the small, dense
heads in a field crop**. A head in `gwhd` is about 57 px across in a 1024 px image — near 1:1 at 1008 —
so the field case is probably not a resolution problem, and asking for a larger side may not change it.
Whether it would is untested.

What it answers is a frame whose heads are separated and large: one spike filling a portrait frame, or
cut heads on straw. What it does not answer is a field quadrat, whichever threshold is sent.

## What that says about the deliverable

A text-prompted segmenter run at one square side is not a field-head counter, and the event should not
be sold as teaching one. The honest claim is the one the API makes: send an image, get boxes and masks
back from a real checkpoint on a real GPU, and spend the week on the data and the application instead of
on the environment. A fine-tune — `sam3.1-` plus a name of its own, served as a second model id — is
where field counts would come from; nothing about the surface changes when one appears.

## Provenance

Measured 17 October 2026 against the pool as it stood that day (`lab-3090`, `lab-5090`, `homepc-4090`
answering), via the same routes a participant calls. `area` in the table (173,777) is the 3090's answer;
the later replay recorded on `lab-5090` printed 173,716 for the same call, which is the difference
`computed_by` exists to explain. The rows are a sample, not a sweep: rows 2–3 of `gwhs-temp` and
`gwfss` row 0 are there because they are the cases that would otherwise look like API failures.
