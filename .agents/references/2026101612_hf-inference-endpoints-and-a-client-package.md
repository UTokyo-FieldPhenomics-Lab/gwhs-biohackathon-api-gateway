# What HF's inference offering is worth to us, and a client package as a deferred idea

Date: 2026-10-16 12:20 JST.
Topic: HF Inference Endpoints as a candidate for the rented backend — its price ladder, the two claims
in the material that do not survive contact with segmentation models, and the one property that would
change the cost model. Plus "ship an importable client, not only curl", recorded as deferred with its
trigger.

## Where this came from

The material arrived as an AI-written summary of Hugging Face's inference offering. Its price ladder
matches HF's own pricing page, so it is recorded here as read; two of its claims do not hold for this
project and are marked below. Nothing here decides anything on its own — it is input to the rental
question the 20th settles.

## The ladder, billed by the minute

| GPU | VRAM | $/h | 120 h |
|---|---|---|---|
| T4 | 14 GB | 0.50 | $60 |
| L4 | 24 GB | 0.80 | $96 |
| A10G | 24 GB | 1.00 | $120 |
| L40S | 48 GB | 1.80 | $216 |
| A100 | 80 GB | 2.50 | $300 |
| H200 | 141 GB | 5.00 | $600 |

Also quoted: a dedicated GPU Space up to 8× L40S at $23.50/h, and Train on DGX Cloud at $2.75/GPU/h
for L40S and $8.25/GPU/h for H100 — the latter is fine-tuning, not serving.

The same ladder as the plan's resource table, so that table needs no change.

## Two claims that do not transfer

1. **"An OpenAI-compatible API server right out of the box."** That is a property of the
   text-generation and embedding containers, not of the platform. Our models are segmentation models:
   ours would be a custom container with our own request and response shape, and there is no
   OpenAI-compatible surface to inherit. The convenience is real for the workload this material was
   written about and absent for ours.
2. **"The recommended path is Inference Endpoints."** Recommended for serving a model to the public,
   which is not what we are doing. [`../rules/project.md`](../rules/project.md) requires a backend to
   be **never publicly reachable**, and a managed endpoint is precisely a public HTTPS URL behind a
   token, on infrastructure we do not control. Adopting it means a rule change or a second recorded
   deviation — the same shape as the tailnet one already written into
   [`../../deploy/README.md`](../../deploy/README.md) §1.3.

## The one property that would change the cost model

`min_replica=0`, scale to zero: the GPU turns off while idle and the bill is per minute of use rather
than per hour of wall clock. For a five-day event with bursts, that is the difference between paying
for 120 hours and paying for the hours somebody actually called.

It does not survive the event either. Waking from zero takes minutes, and a participant waiting minutes
for a first call is worse than the money saved, so inside the window the setting is `min_replica=1` and
the flat estimate stands. Where it does pay: **rehearsal and load testing between now and December**,
and any stretch the pool is expected to sit idle — which is most of the next seven weeks.

## What it does not change

Nothing about transport. A managed endpoint joins neither the gateway's VPC nor the tailnet, so it is
the one candidate that satisfies none of the three transports the plan allows. If it is ever adopted,
the deviation is the entry to write, not the protocol.

## Deferred: a client package instead of only curl

Noted for later: *our datasets API should also ship an importable package, not only curl, so a result
can be fetched directly* — after the event's own work, not now.

**Why it deserves care rather than a small yes.** A client is a second definition of the contract.
[`../rules/docs.md`](../rules/docs.md) already refuses a second rendering of the reference for exactly
that reason, and a hand-written client with its own response models is the same hazard. The
repository's own precedent points the same way: the token CLI is a subcommand of the same installed
package *so that the writer and the reader share one Pydantic model rather than two definitions of
it*.

**The cheap version, if it comes.** One module wrapping the two calls — resolve an address, take the
`data.*` URL, post to the model surface — importing the response models rather than restating them.
Not a package with its own `pyproject.toml`, lock file and CI job, which the
one-pyproject-per-subproject rule would otherwise require, and not for two endpoints that a dozen lines
of `httpx` already cover.

**Its trigger.** A participant hand-rolling a client and getting the RLE decoding or the address
resolution subtly wrong, or a second language wanting the same wrapper. Until then the client is the
downloadable snippets the documentation site already ships, and they need no version to stay true.
