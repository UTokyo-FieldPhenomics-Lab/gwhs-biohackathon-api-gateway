# The stack the proposal promised, and what the API is now

Date: 2026-10-17 12:50 JST.
Topic: the project description published on de.NBI still advertises a Hugging Face Space reached through
Gradio and preconfigured Docker environments on Renku; neither is a delivery path. What the page says,
what is actually true today, the verified state of Renku's public compute tier, why a notebook platform
cannot carry this project, and the framing for the 2026-10-20 report.

## What the description page still says

<https://www.denbi.de/de-nbi-events/2137-5th-biohackathon-germany-global-wheat-head-segmentation-gwhs-towards-spikelet-counting-and-fusarium-detection-on-a-smartphone>

Three claims, quoted:

- the community "will provide an annotated GWHS dataset and a collection of existing models through the
  Global Wheat Hugging Face Space, accessible via Gradio APIs";
- "Preconfigured Docker environments on the Renku platform and a Jupyter Notebook tutorial";
- "Smartphone-based phenotyping is where this is heading".

The page is in de.NBI's CMS, so it is not ours to edit; whether it can be updated is a question for
`contact@denbi.de`, not an edit to make.

## What is true today

**Hugging Face.** Probed through the Hub API on 2026-10-17: the `GlobalWheat` org holds two models
(`GlobalWheat/GWFSS_Segformer_b0`, `..._b4`) and one dataset (`GlobalWheat/GWFSS_v1.0`), and **zero
Spaces**. A Hub-wide search for `globalwheat` returns two unrelated third-party Spaces and no org Space.
So the "HF Space, accessible via Gradio APIs" is not stale wording for something that moved: the entry
point does not exist. HF is now a source of weights and data, not a place a participant calls.

**Renku.** The string appears nowhere in this repository — not in a plan, not in a rule, not in a
reference. Dropping it was never recorded as a decision, which is why the page still promises it.

**The API that does exist.** Probed on 2026-10-17:

| Address | Answer | Means |
|---|---|---|
| `api.global-wheat.com/health` | 200 `{"status":"ok","version":"0.1.0"}` | the gateway is serving |
| `api.global-wheat.com/models` | 401 `invalid_key` | the key gate is in front of the model surface |
| `data.global-wheat.com/` | 403 from S3 | the bucket is private and only the distribution reads it |
| `docs.global-wheat.com/` | 200 | the participant site is published |

Routes: `/datasets`, `/datasets/{dataset}`, `/datasets/{dataset}/images`, `/datasets/{dataset}/images/{row}`,
`/models`, `/models/{model}`, `/models/{model}/predict`, `/models/{model}/predict/upload`, plus the two
open pages `/docs` and `/play`. One token, `X-API-Key`, covers the dataset and the model surfaces.

## Renku, verified rather than assumed

Renku is the SDSC's reproducible-data-science platform: a session is a container (JupyterLab, VSCode or
RStudio) launched from a project, with the project's code, Docker environment and data connectors mounted.
The free public compute tier is always free; interactive sessions auto-pause after 2 hours idle and shut
down after 2 weeks.

The public resource pool, read from the class selector in
<https://docs.renkulab.io/en/stable/docs/users/compute/resource-pools-and-classes> on 2026-10-17 — the
classes are published as a screenshot, so this was read from the image row by row:

| Class | CPU | RAM | Disk | GPU |
|---|---|---|---|---|
| `small` | 0.25 | 1 GB | 64 GB | 0 |
| `medium` | 0.5 | 2 GB | 64 GB | 0 |
| `large` | 1 | 4 GB | 128 GB | 0 |
| `xlarge` | 2 | 8 GB | 128 GB | 0 |

Every class is `0 GPUs`, and the largest is two cores and 8 GB — a teaching environment, not a machine
that segments ~3000 spikes. GPU exists on two paths, neither of which is the default: a custom resource
pool negotiated with SDSC and priced at infrastructure cost, or the CSCS integration shipped in October
2025, which needs a CSCS account, a separate registration for the integration, and spends the user's own
CSCS quota.

The earlier characterisation — "like Colab, Jupyter, no GPU by default" — holds with two corrections.
Similar: browser-launched notebook, free tier, idle culling. Different: the unit is a Git project plus a
pinned Docker image plus data connectors, not a throwaway notebook, and the same resource classes also
run background Jobs, so "notebook" undersells what it is and "Colab" undersells what it costs to keep
true.

## Why it cannot be the delivery path

The distinction is not teaching versus production, and not compute size. It is **addressing**.

- **A session is private to the person who launched it.** Five participants are five containers; there is
  no shared, always-on service for a team's application to call.
- **A session has a lifetime.** Idle pause after 2 hours, deletion after 2 weeks. Anything built on one is
  gone the next day.
- **The project's own goal is a phone.** The description page says smartphone-based phenotyping; a phone
  cannot start a Renku session. It can only call an HTTPS endpoint.

So the sentence to keep is: *Renku hands a participant a machine; this project has to hand them an
address.* That is what a workspace cannot give and an authenticated endpoint can.

Keeping Renku as a teaching mirror is defensible, and honest only with its cost stated: a pinned
environment drifts from the contract, and a frozen image answering `404` months later is blamed on the
API. [`../rules/docs.md`](../rules/docs.md) already refuses a second rendering of the reference for the
same reason — "a second rendering of the same contract would be a second thing to keep true" — and a
Renku environment is that second rendering in container form. The marginal value left is "a workspace
where someone can keep multi-step code", which is a course need; a participant who only wants to see a
call run already has `/play`, no account, no install.

## What replaces them, already live

- `/play` — run one model from the browser, same-origin with the API, no account, backed by a `curl`
  line the page prints.
- `/docs` — the API's own OpenAPI reference, framed by the site, with a field for the key.
- Post-processing snippets in Python, JavaScript and Kotlin, downloadable, with one decoder file the API's
  own page also loads, so a demo and a tutorial cannot disagree about a correct decode.

## What is still owed

State these before they are asked:

- **No participant notebook and no preconfigured environment.** The Jupyter tutorial promised on the page
  is not being delivered. The replacement is a static site plus a browser page, which covers "run it once"
  and not "write multi-step code in a prepared environment".
- **One model, and it is not a wheat model.** `sam3.1-meta` (SAM 3.1) answers today; the "collection of
  existing models" is a collection of one.
- **It fails where the dataset is hardest.** On `gwhd` row 0 — a 1024 px field quadrat with 144 annotated
  heads — it returns 0 objects at every threshold down to 0.05, while segmenting a single conspicuous
  spike or cut heads on straw. The numbers are in
  [`../../docs/content/models/index.md`](../../docs/content/models/index.md).

## The report, in three sentences

1. The proposal's two serving promises — a Gradio Space and a Renku notebook environment — have become one
   authenticated HTTP API, live at `api.global-wheat.com`, with the images on `data.*` and the guides on
   `docs.*`.
2. The reason is addressing, not compute: Renku's public pool tops out at 2 cores / 8 GB / 0 GPU, its
   sessions are private and expire, and a phone cannot start one — so the participant-facing surface is an
   endpoint, and the Docker environments moved to the GPU machines behind it.
3. What that costs: no participant notebook, one model rather than a collection, and a measured weakness on
   field-scale images. A Renku environment can be offered as an optional teaching mirror, explicitly not a
   delivery path and not promised to survive to December.

## Caveats to keep

- The public-pool table is the documentation as of 2026-10-17. It may have differed when the proposal was
  written; say "the public pool is", not "it has always been".
- The 4-class table is read from a screenshot in the docs; the CSCS path is from the October 2025 release
  note (<https://blog.renkulab.io/release-2025-10>).
- The description page is de.NBI's; correcting it is a request, and until it is granted the page and the
  API disagree in public.
