# Privacy sanitisation: one key, twenty files, and the history

A collaborator reached the public repository, read a load script's hardcoded token, and got a `200`
from the production gateway with it. The token is the only live credential in git, but the same files
carry the tailnet and LAN addresses of the three backends, the room routers' node names, and ssh
users. A committed address is permanent: rewriting `main` does not reach a clone, a fork, or an
archive that already holds it, and it does not un-leak a token that still works.

Two rules came out of it and are already written: the constraint in
[`../rules/project.md`](../rules/project.md) §Security, and the pre-commit check in
[`../../AGENTS.md`](../../AGENTS.md) §Commits.

## Steps

- [ ] Commit the pending plan file and the `models/` pointer; back up before anything is rewritten
- [ ] Rotate the leaked team's token; update `.env`; confirm the old one in the host's journal
- [ ] Write `.agents/local/pool.md`: every address, router name, ssh user and host path
- [ ] Ignore `.agents/local/` and the raw `gateway.log` files
- [ ] `git rm --cached` the seven logs; `run_meta.json`'s `host` becomes `gwhs-host`
- [ ] Rewrite the 25 files (121 occurrences) to aliases; the five scripts read `GW_HOST` / `GW_POOL`
- [ ] Point `rules/backend-pool.md` and `AGENTS.md` at `.agents/local/pool.md`
- [ ] Rewrite history: `--replace-text`, `--replace-message`, `--invert-paths` for the logs
- [ ] Verify: zero literals in history, the three test suites, `zensical build --strict`
- [ ] Force-push; re-clone the host and this checkout; confirm Pages rebuilt
- [ ] Land the pre-commit leak check

## Decisions

1. **Aliases, not invented addresses.** `lab-3090`, `lab-5090` and `homepc-4090` are already the
   names `computed_by` publishes and the docs table prints, so an address becomes the name the
   repository already uses for that machine. A placeholder such as `192.0.2.10` would be worse than
   a stale real address: it looks usable, and nothing reveals that it is not.
2. **The mapping is untracked and local.** `.agents/local/pool.md` holds alias → address, router node
   name, tailnet address, ssh user, key path. Ignored like `.env`, so an agent can still resolve a
   backend and a reader of the repository gets a name that resolves nowhere.
3. **Raw logs leave; aggregates stay.** The seven `.log` files are 2.1 MB and carry 8 391 of the
   8 4xx address occurrences. `client.csv`, `health.csv` and `summary*.json` are alias-only and are
   the evidence behind the numbers the references quote, so they stay tracked.
4. **The history is rewritten.** 244 commits, 9.9 MB, one branch, no tags, no forks, no PRs — the
   cheapest possible case for `git-filter-repo`.
5. **This plan stays tracked.** It is the design record for the compute plane and six documents link
   to it, so its own address lines are rewritten like any other file's. The one thing that leaves it
   for `.agents/local/evidence.md` is the AWS support case number, which is operational detail rather
   than a design decision and ties a public file to a specific account.
6. **An alias never stands where an address stood.** `http://lab-3090:12711` reads as dialable, and
   that is the shape an agent tries first; a cell under a "Private address" column still claims to be
   an address; a bare `lab-3090` in a sentence is safe only because it is plainly a name. So an
   address inside a URL becomes "the backend on `lab-3090`, port `12711`", and the load scripts take
   `GW_HOST` / `GW_POOL` and **stop, naming `.agents/local/pool.md`, when either is unset**: a
   fallback would silently dial the wrong card, and a stopped run is the cheaper failure.

## The mapping

The check is by shape, not by a list: **any IPv4 outside `192.0.2.0/24`, `198.51.100.0/24`,
`203.0.113.0/24`, `127.0.0.1`, `0.0.0.0` and `169.254.169.254` is a finding.** An earlier list of "our"
addresses missed the gateway host's own Elastic IP, which is the most useful one in the repository.
**Order matters when substituting**: the longest literal first, so an address that is a prefix of
another is replaced after the longer one. The exact strings are in `.agents/local/pool.md` and are not
repeated here — a plan that lists the addresses is the leak it describes.

| In the repository | Becomes |
|---|---|
| the participant token | removed; the script reads `GW_KEY` |
| the gateway host — tailnet address, Elastic IP, VPC address, EC2 private DNS name | `gwhs-host` |
| each backend's LAN address | its alias: `lab-3090`, `homepc-4090`, `lab-5090` |
| a room's LAN, and the Docker Desktop proxy behind `room-router-d` | `the laboratory LAN`, `the home LAN`, `the 5090's LAN`, `room subnets`, `the 5090's own proxy` |
| the campus egress and its `/24` | `the campus client network` |
| addresses that appear in a log because someone outside connected | `a client`, `a scanner` |
| a router's node name | `room-router-a` … `room-router-f`; the home router `home-router` |
| the laboratory PC's host name and tailnet name | `labpc-host`, `labpc-tailnet` |
| the 5090's ssh alias | `lab-5090` |
| an ssh user, and a path under the host's home | `<user>@`, `$HOME/…` |

## Verification

```bash
git grep -nE '<the same 45 literals>' -- .            # nothing
git log --all -p --pretty=format: | grep -E '<the same 45 literals>' | wc -l    # 0
uv run --directory gateway pytest && uv run --directory gateway mypy && uv run --directory gateway ruff check
uv run --project docs zensical build --strict
```

## Risks

- **A local `.git` survives a force-push.** Only a re-clone — this checkout, the host's `/opt/…` —
  actually drops the old objects; `reflog expire` plus `gc` leaves them reachable.
- **GitHub does not collect unreachable objects at once**, so an old SHA stays readable for a while.
  With 0 forks and 0 issues, deleting and recreating the repository is the clean purge and costs
  almost nothing.
- **Ignored paths are not retroactive.** The `.gitignore` entries stop the next commit, not the ones
  already written; that is what the `--path --invert-paths` pass is for.
- **Text substitution can reach code.** A literal that also appears in an assertion or a doc example
  changes behaviour, so the three test suites and the strict docs build are part of the rewrite, not
  an afterthought.
- **The `models/` gitlink moves.** Commit the pointer before rewriting, or the submodule state is
  lost to `reset --hard`.
- **`git clean -xdf` deletes the only copy.** `.agents/local/` is untracked by design, so the
  authoritative addresses must also exist outside the checkout — `~/.config/biohack-gateway/` on the
  host is the natural second copy, since that is where `models.json` already holds the URLs.
