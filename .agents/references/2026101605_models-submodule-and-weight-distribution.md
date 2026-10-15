# The models submodule, and how a weight reaches a backend

Session record for 2026-10-16. The constraints this produced are in
[`../rules/models.md`](../rules/models.md) and the design invariants in
[`../rules/project.md`](../rules/project.md); this file holds the reasoning, the commands run, and
the results observed.

## What was decided

1. **This repository stays public; the containers live in a private repository linked as `models/`.**
   The two audiences are opposite: participants need the OpenAPI surface, the examples and the
   skills, while the containers carry partner code and the weights' location. A submodule is the
   only mechanism that keeps one of those private without giving up the other.
2. **Weights live in a Drive folder shared by link.** No revision pinning, no checksum, no manifest:
   the containers are rebuilt and frozen before the event, and a participant addresses a model by its
   id and never by a revision. A weight revision would describe something no caller can see.
3. **A model's runtime identity is its container image digest.** Nothing has to be computed, which
   keeps the no-hashing rule in `project.md` intact.

### Why not the alternatives

| Considered | Why it lost |
|---|---|
| Make this repository private, containers as a plain directory | Costs a paid plan to keep publishing Pages from a private repository, loses free native secret scanning, loses the public contribution surface, and breaks `deploy/prepare-host.sh`, which bootstraps with an anonymous `curl` of the script and an anonymous `git clone` (lines 6 and 44). |
| `models/` as a symlink to a sibling clone | The path works, but the contents become invisible to code search: `rg` follows neither a symlink nor an ignored path, so `grep`/`glob` from the repository root return nothing and report no error. An agent asked where something lives answers "nowhere". Measured, see below. |
| `models/` as a `.gitignore`d directory | Same failure: `rg` skips ignored paths by default. Measured. |
| `git subtree` | Inlines the private containers into this public repository's history. |
| Invert it: private source, publish a filtered mirror | Two repositories to keep in step for a three-day event, and the contract is already the public half. |

## What was verified

Fixtures under `/tmp/opencode`; `git 2.55.0`, `ripgrep 15.2.0`. Nothing here is inferred from
documentation.

**`update = none` is what makes an inaccessible submodule survivable.**

```
$ git -c protocol.file.allow=always clone --recurse-submodules super.git out
略过子模组 'models'          # skipped, and the clone succeeds
exit=0

$ git submodule update --init --checkout models     # the owner's route still works
子模组路径 'models'：检出 '7bb5796'
```

**Search tools do not see a symlinked or ignored directory.** This is what eliminated the symlink
option, and it fails silently rather than loudly.

```
$ rg NEEDLE_MARKER repo/            # repo/models -> ../realmodels
No matches found
$ glob '**/*.py' repo/              # same fixture
gateway.py, submod/a.py             # models/src/train.py missing
$ rg NEEDLE_MARKER repo/models      # naming the symlink itself does work
models/src/train.py:2
$ rg --no-ignore NEEDLE repo/       # an ignored real directory needs this too
```

**A detached-HEAD commit is invisible in this repository and fails for the next cloner.** The whole
chain, in order:

```
$ git -C models add -A && git -C models commit -m edit
commit exit=0                       # succeeds, belongs to no branch
$ git -C models push
致命错误：您当前不在一个分支上。     # exit 128
$ git status --short --branch       # in the superproject
## main...origin/main [领先 1]      # nothing about the submodule
$ git submodule status
 38c74582f345... models             # no '+' — matches the recorded pointer, so it looks clean
$ git add models && git commit && git push      # the broken pointer is published
$ git -C fresh submodule update --init --checkout models
致命错误：git upload-pack: not our ref 38c74582f345...
exit=128                            # the next person's clone fails
```

The correct order was then run end to end and reproduces: `git -C models switch main` → commit →
`git -C models push origin main` → `git add models` → commit → push → a fresh clone checks the same
commit out with `exit=0`.

**`--remote` alone does not override `update = none`; `--checkout` does.**

```
$ git submodule update --remote models
略过子模组 'models'                  # exit 0, nothing happens, no warning
$ git submodule update --remote --checkout models
子模组路径 'models'：检出 '7bb5796'   # this is the command that works
```

## What broke, or would have

- **`git status` is not enough to notice a submodule problem.** Neither `git status` nor
  `git submodule status` in the superproject mentions an unpushed submodule commit, because the
  recorded pointer and the submodule's `HEAD` agree. Only a clone reveals it.
- **A repo-level choice could have hidden the failure instead of fixing it.** `.gitignore` and a
  symlink both look like they solve "keep it out of the public history" while quietly removing the
  code from every agent's view.
- **Weights must not reach either repository.** GitHub refuses a file above 100 MiB and warns above
  50 MiB, and a weight in the private repository's history would have to be removed by rewriting it,
  which invalidates every pointer recorded here. The submodule's `.gitignore` was written before any
  contributor could push one.
- **Drive, as a runtime source, has three problems the tool documents itself.** `gdown`'s FAQ asks for
  "Anyone with the link" sharing (so a restricted file needs browser cookies on the machine that
  builds), reports that Google throttles downloads when many people fetch one file and terminates
  large transfers after about an hour, and defaults `--retries` to zero. A public link on a public
  repository would publish the weights, which is why the link is confined to the private submodule
  and the download happens on the build host, not inside the image.

## What is not decided

- **The gateway-to-backend protocol.** `gateway.md` states that a backend sits "behind one uniform
  protocol" and that normalisation happens in the container, but no transport is defined and the
  model surface is not written — the application registers four dataset routes and `/health` and
  nothing else. Containers cannot be built against it yet, so contributors are asked for an entry
  point with a fixed signature, and containerisation waits for the surface.
- **Where the containers are built and pushed.** The weights are baked in, which keeps a Google
  session and the Drive link off every backend host and needs only registry credentials there. If
  rebuilding to change weights proves to be a bottleneck, the alternative is a code-only image with
  the weights mounted, which makes the recorded revision load-bearing again.
