# A skill file a participant's agent can fetch

The docs assumed a human reading a page and typing a curl. A participant working with an AI agent
needs a compact set of instructions the agent can load before it starts: the base URL, the key
convention, discovery routes, the three model-call routes, the answer shapes and the limits. That is
a skill, not another manual page.

## What was added

- **`.agents/skills/global-wheat-api/SKILL.md`** — a public skill with `name` and `description`
  frontmatter. It links the OpenAPI and the docs rather than copying the full schema, names the
  dataset and model discovery routes, explains `predict`/`predict/upload`/`generate`, lists the
  answer fields, and records the three-inference and failure policy.
- **`docs/content/examples/agent-skills.md`** — the participant page. Its one-line prompt points at
  the raw `SKILL.md` on `main`; the page explains what the agent gets and what it will not do.
- **`AGENTS.md`** — `.agents/skills/` is now a documented kind of working note, beside rules,
  references and plans. The tree and the “Working Notes” section say what a skill is and that it is
  public.

The raw URL is
`https://raw.githubusercontent.com/UTokyo-FieldPhenomics-Lab/gwhs-biohackaton-api-gateway/main/.agents/skills/global-wheat-api/SKILL.md`.
It starts working when the file is pushed to `main`; the page and the skill are otherwise independent
of the site build.

## Why `.agents/` and not `docs/content/`

A `SKILL.md` is a file an agent reads as instructions, not a page a browser renders. Keeping it in
`.agents/skills/` matches the repo's existing agent knowledge and leaves the docs site to carry the
human-facing page that points at it. The skill has no credential and no private address, so it is
safe in the public repository.

## Verification

`zensical build --strict` renders the page and the Examples nav entry; the relative-link check finds
nothing. The prompt is a raw GitHub URL, so the page cannot be checked against a live fetch until the
skill is committed and pushed; that is the one deployment step this change needs.

## What is left

The skill repeats a few facts that live in the service (the full datasets, the 404 for `gwhs`). It
points to `GET /datasets` and `GET /models` as the authority, but if those facts change, the skill is
a second copy to update. The same is true of the failure policy; the link to `api/http.md` is the
long form.
