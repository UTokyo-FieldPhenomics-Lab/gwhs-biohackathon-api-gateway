# Weights fetched inside the container build

Agreed 2026-10-16. The container must be handable to another machine as a small archive that the
target builds itself, because the alternative — `docker save | docker load` — moves tens of
gigabytes over one sender's uplink, serially, with no resume, while N build hosts each have their own
downlink and can build in parallel.

## Steps

- [x] `models/sam31_official/Dockerfile`: replace `COPY weights/ /weights/` with an ARG'd fetch + byte assertion
- [x] `models/sam31_official/.dockerignore`: ignore `weights/`
- [x] `models/sam31_official/weights/README.md`: it is the smoke test's cache, not the build's payload
- [x] `models/sam31_official/README.md` and `models/README.md`: the fetch, the bytes, and how a container is handed over
- [x] parent `.agents/rules/models.md`: the link is now in a layer; identity is the commit + the asserted id
- [x] reference note, and push the submodule before recording its pointer

## The decision

One model is one archive: `git -C models archive HEAD:sam31_official` gives a zip of the tracked
files — code, `vendor/`, the lock, the Dockerfile — and no weights, no `.venv`. The target unzips and
runs `docker build`, which fetches its own checkpoint by file id and asserts its byte count.

## What this trades away, taken deliberately

- **The link is now in an image layer.** Anyone who receives the image receives the store's id. Taken
  because the store is share-by-link and revocable, and because this image reaches GPU backends and
  never a participant nor a public artifact.
- **A rebuild is only as faithful as that one file.** The byte count is the assertion, not a hash:
  `project.md`'s "a content hash returns only against a measured failure" still holds, and the ARG
  itself is what busts the layer cache when the id changes.
- **The image digest is no longer the sole identity of the weights.** For an image moved as an image
  it still is; for a build repeated per host, identity is the submodule commit plus the asserted id.

## Rejected

- **A GitHub token in the Dockerfile for `git clone`.** The code is 5 MB; the token would be a
  push-capable credential in a layer on machines we do not own. The scp/zip step already solves it.
- **`RUN --mount=type=cache` / `--secret`.** The build host here has no buildx plugin; `docker build`
  is the legacy builder, where both flags are hard errors. The uv cache is removed in the same `RUN`
  instead, which needs no BuildKit.
- **A registry for delivery.** GHCR egress is billed, and a per-machine build is free.
