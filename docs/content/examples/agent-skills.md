# Agent skills

You can send the sentence below to your agent, and it will fetch a skill file that gives it the
API's datasets, models, parameters, limits and decoders. Make your team key available to the agent as
`GLOBAL_WHEAT_API_KEY` first; the agent reads it from the environment and must never print it.

```text { .wrap }
Read https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/main/.agents/skills/global-wheat-api/SKILL.md and use the Global Wheat BioHackathon API for me. My team key is in GLOBAL_WHEAT_API_KEY; never print it. Ask me before a Nano Banana generation, because it spends credits.
```

That is the whole setup. The agent reads the skill once and can then discover the data and models,
call a prediction on a row or on your own photograph, generate a picture, and decode the answer with
the client files.

## What the skill gives it

- `GET /datasets` and `GET /models` for discovery, so it does not hardcode a row count or a model
  list. `gwhs` is not served yet and answers `404`; `gwhs-temp` is the development sample.
- `POST /models/<id>/predict` for a published row, `/predict/upload` for a photograph, and
  `/generate` for the Nano Banana ids.
- The model's own parameters, read from `GET /models/<id>`; a geometry parameter is one JSON array,
  not repeated query names.
- A task-to-model table — masks, instances, organs, counting, features, COCO detection, prose,
  pictures — so it does not have to read every model page before choosing one.
- The answer shapes: objects and COCO masks, `count` and `density`, `embedding` and `features`,
  `answer` and `image`.
- A read-next map: the exact schema is the OpenAPI, the parameters are `GET /models/<id>`, and the
  measurements are the model pages.
- The client files for decoding, and the three-inference limit with the `429`/`503`/`504` policy.

## It reads the live contract

The skill is deliberately an index, not a frozen copy of the schema. It tells the agent to read
[`openapi.json`](https://api.global-wheat.com/openapi.json), `GET /models/<id>` and
`GET /datasets/<id>` before a call it has not made before. An agent with HTTP access will do that;
if yours cannot browse, paste the OpenAPI document into its context as well.

## What it will not do

It does not print or send your key anywhere except the API's own origin, and it does not call
`generate` without asking: a generated picture is a paid sample. It also does not invent a dataset
or a row; a call for `gwhs` answers `404` until its annotation is released.

## If your agent supports a skills directory

Download [`SKILL.md`](https://github.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/blob/main/.agents/skills/global-wheat-api/SKILL.md)
into that directory instead of pasting the sentence. The file carries `name` and `description`
frontmatter and the same instructions; the sentence is only a pointer to it.

## The short version

If your agent already knows the API, this is enough:

```text { .wrap }
Read https://docs.global-wheat.com/api/http/ and use the Global Wheat API for me; my key is in GLOBAL_WHEAT_API_KEY, and ask before a Nano Banana generation.
```

The full skill adds the model families, the decoder advice and the failure policy. The
[OpenAPI document](https://api.global-wheat.com/openapi.json) is the field-level contract underneath
both.
