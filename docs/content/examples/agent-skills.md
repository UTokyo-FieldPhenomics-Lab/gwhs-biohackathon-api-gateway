# Agent skills

The skill file is an index of this API written for an agent: how to discover datasets and models, the
three routes that run one, the shape of an answer, where the client decoders are, and the limits. It
points at the live contract rather than copying it.

## Give it the key

Make your team key available to the agent as `GLOBAL_WHEAT_API_KEY`. The skill tells it never to print
the key or send it anywhere except `api.global-wheat.com`, and to ask before a Nano Banana generation,
because that spends credits.

## The sentence

Send this to your agent:

```text { .wrap }
Read https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/main/.agents/skills/global-wheat-api/SKILL.md and use the Global Wheat BioHackathon API for me. My team key is in GLOBAL_WHEAT_API_KEY; never print it. Ask me before a Nano Banana generation, because it spends credits.
```

That is the whole setup. From there the agent reads `GET /datasets` and `GET /models`, picks a model
for the task, calls `predict`, `predict/upload` or `generate`, and decodes the answer with the client
files.

## It reads the live contract

The skill is deliberately an index, not a frozen copy of the schema. It sends the agent to
[`openapi.json`](https://api.global-wheat.com/openapi.json), `GET /models/<id>` and
`GET /datasets/<id>` before a call it has not made before. An agent with HTTP access will do that; if
yours cannot browse, paste the OpenAPI document into its context as well.

## If your agent supports a skills directory

Download
[`SKILL.md`](https://github.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/blob/main/.agents/skills/global-wheat-api/SKILL.md)
into that directory instead of pasting the sentence. The file carries `name` and `description`
frontmatter and the same instructions; the sentence is only a pointer to it.
