# Models Rules

Directory: `models/` — a git submodule, not part of this repository's own tree. Repo-wide invariants
are in [`project.md`](project.md) and are not repeated here; what a container must answer and where
normalisation belongs are [`gateway.md`](gateway.md)'s rules.

- **The containers are private; the contract is not.** `models/` points at a private repository
  holding the GPU backends' containers, their model code, and where their weights come from. The
  split is the point: a participant reads the contract in this repository and the site it publishes,
  and never needs anything under `models/`.
- **`.gitmodules` carries `update = none`, and that is load-bearing.** With it, a clone for someone
  without access to that repository still succeeds: `git clone --recurse-submodules` reports the
  submodule as skipped and exits 0. Without it the same clone stops on an authentication prompt and
  reports the repository as not found, which reads as this repository being broken.
- **This repository must build and test with `models/` empty.** Nothing here imports, reads, or
  generates from it. A workflow's own `GITHUB_TOKEN` reads only its own repository, and a pull
  request from a fork receives no secrets at all — so a build that needed the submodule would break
  every contribution from outside the organisation.
- **Push the submodule before recording the pointer, never after.** The value recorded here is a
  commit id. A pointer to an unpublished commit yields a repository that looks clean locally and
  fails for everyone else with `upload-pack: not our ref`, so the failure lands on the next person to
  clone rather than on the commit that caused it.
- **`git -C models switch main` before the first edit.** A submodule is checked out detached, so a
  commit made there belongs to no branch; `git -C models push` then refuses with "you are not
  currently on a branch", while neither `git status` nor `git submodule status` in this repository
  reports anything wrong. The unpushed commit is invisible until someone else clones.
- **Bring it up to date with both flags: `git submodule update --remote --checkout models`.** With
  `update = none` in `.gitmodules`, `--remote` alone is skipped in silence, because the update
  *procedure* still comes from the configuration; `--checkout` is what overrides it.
- **Bump the pointer when the contract moves, not on every model commit.** It records which model
  revision the published model ids answer from. A container rebuilt with different weights is not by
  itself a reason to touch this repository.
- **Weights are never committed, in either repository.** GitHub refuses a file above 100 MiB
  outright, and a weight that reaches history is permanent: removing it rewrites the private
  repository and invalidates every pointer recorded here. The submodule's `.gitignore` blocks the
  usual extensions before the mistake is possible.
- **The weight store is a Drive folder shared by link, and the link never appears in this
  repository.** This repository is public, so the link is a credential. It belongs to the private
  submodule alone, and the weights are downloaded onto the build host and copied into the image
  rather than fetched by the image, so no image layer carries it either.
- **A model's runtime identity is its container image digest.** Weights are pulled as "latest" by
  decision and carry no recorded revision or checksum: the containers are frozen before the event,
  and a participant addresses a model by its id. Nothing here computes or stores a weight hash, per
  [`project.md`](project.md).
- **The submodule's own `AGENTS.md` points back here and repeats nothing.** Contributors work in that
  repository, so its entry point links this repository's `AGENTS.md` and `rules/`; a second copy of
  these constraints would be a second thing to keep true.
