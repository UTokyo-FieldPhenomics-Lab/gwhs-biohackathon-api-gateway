# The token command line

Status: implemented 2026-10-14 — both halves: the writer is `biohack-gateway tokens`, and the reader is
the request gate in
[`2026101415_✅api-auth-and-error-contract.md`](2026101415_✅api-auth-and-error-contract.md).
Owner files: `gateway/src/biohack_gateway/tokens.py`,
`gateway/src/biohack_gateway/cli.py`, `gateway/pyproject.toml`, `gateway/tests/test_tokens*.py`,
plus the docs that name the command.

## Why this and not an endpoint

Issuing a token is a human action taken a handful of times before an event and a few times during it.
A public surface whose whole purpose is to mint credentials would be the most dangerous thing on the
host, would need a second secret to protect itself, and would enter an API contract that is frozen for
the event. A command on the host uses the path that already exists — SSH or the tailnet, addressed and
key-only — and it can run as the same user that owns the pool file, so there is no privilege boundary
to cross and no service account to invent.

It is also the producer half of the authentication work: the file this writes is the file that work
will read, so writing it first means the reader starts from a real artifact and a real model instead
of a stub.

## Decisions

- **The pool is a flat `{team_id: token}` JSON object.** No per-team fields: quotas are not counted
  (see below), so there is nothing else to store. Adding a field later is one additive migration.
- **Tokens are stored verbatim.** This repository does not compute or store hashes, and a verbatim
  token is what makes the file reusable across instances and handable to the team it belongs to.
- **The host's copy is authoritative** and the copy kept off-host is refreshed by one `scp` after any
  change (`.agents/rules/deploy.md`). The command prints that reminder rather than performing it,
  because it cannot know where the other copy lives.
- **No counting, so no quota.** The limits are a global and a per-key concurrency gate plus a per-key
  rate limit, all in process memory: a count would need persistence, a lifetime, and a top-up path,
  and its only effect would be a human round-trip when a team runs out. Attribution comes from the
  team id in the request log instead.
- **Standard library only.** `argparse`, `json`, `secrets`, `os.replace`, `tempfile`: the command has
  no reason to grow dependencies, and Pydantic is already there for the pool's shape.
- **stdout is the value, stderr is the narrative.** `tokens add` prints only the token, so
  `… add --id gw-01 > token.txt` does what it looks like; the file path and the two reminders go to
  stderr.
- **The command never calls `systemctl`** and never hard-codes a host path: the file defaults to
  `~/.config/biohack-gateway/tokens.json` and `--file` overrides it, so the same binary runs on a
  laptop against a copy.

## Steps

1. `gateway/src/biohack_gateway/tokens.py` — `TokenPool` (a `RootModel[dict[str, str]]` that validates
   team ids and tokens), `load` (missing file is an empty pool), `save` (parent directory 0700 when it
   creates it, file written beside the target and moved into place at 0600), `issue` (generated or
   given token, refused unless `replace`), `revoke`.
2. `gateway/src/biohack_gateway/cli.py` — `biohack-gateway tokens list | add --id … [--token …]
   [--force] | remove --id …`, exit 1 on a domain error and 2 from argparse.
3. `gateway/pyproject.toml` — `[project.scripts] biohack-gateway = "biohack_gateway.cli:main"`.
4. Tests first: `tests/test_tokens.py` for the pool and `tests/test_tokens_cli.py` for the command.
5. Docs: `deploy/README.md` §1.5 and `.agents/rules/gateway.md` say the writer exists and the reader
   is still to come; `project.md`'s security bullet stops claiming a per-key quota and names what is
   enforced instead.

## Verification

```bash
cd gateway
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run biohack-gateway tokens add --id gw-01 --file /tmp/opencode/tokens.json
uv run biohack-gateway tokens list --file /tmp/opencode/tokens.json
uv run biohack-gateway tokens remove --id gw-01 --file /tmp/opencode/tokens.json
```
