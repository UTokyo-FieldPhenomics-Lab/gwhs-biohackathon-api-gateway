# The weights are fetched inside the container build

2026-10-16. Changed: `sam31_official/Dockerfile` no longer copies a staged checkpoint in; it fetches
one by file id and asserts its byte count. The rules this moved are
[`../rules/models.md`](../rules/models.md); this note is the detail, the measurements, and the
mistakes.

## Why

The goal is handing one model to another machine cheaply. The alternatives were measured rather than
argued:

- `docker save | docker load` of a ~36 GB image moves tens of gigabytes over **one sender's uplink**,
  serially, and the stream has no resume. At 20 MB/s that is ~30 minutes per host, and a break at 90 %
  starts again.
- Building on each host costs one download per host, **on that host's own downlink**, in parallel, with
  its own layer cache afterwards. N hosts need N downloads, but never the same download twice.

For a five-day event, simplicity of deployment won over the reproducibility argument that the previous
design was built on. The trade is recorded in the rules; the short form is that the store's link is now
in an image layer, and identity for a repeated build is the commit plus the asserted id rather than the
image digest alone.

## Measured, on the workstation, 2026-10-16

```
$ stat -c %s weights/sam3.1_multiplex.pt
3502755717
$ sha256sum weights/sam3.1_multiplex.pt
0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6
```

The byte count is what the `Dockerfile` asserts and what `models/README.md`'s table now carries. **The
hash is recorded here and nowhere else**: nothing computes or stores one, per `project.md`, and a
content hash returns only against a measured failure — a wrong or replaced checkpoint that matches the
byte count exactly. If that happens, this line is where to start.

### The build host has no buildx, which decided the RUN's shape

```
$ docker version --format '{{.Server.Version}}'
29.7.2
$ docker buildx version
docker: unknown command: docker buildx
$ ls -l ~/.docker/cli-plugins/docker-buildx
… -> /usr/lib/docker/cli-plugins/docker-buildx   # broken symlink
$ docker build --help | head -3
DEPRECATED: The legacy builder is deprecated and will be removed in a future release.
```

`RUN --mount=type=cache` and `RUN --mount=type=secret` are hard errors under the legacy builder, so the
Dockerfile uses neither. The uv cache is deleted in the same `RUN` instead, which is what keeps the
fetcher out of the layer that carries the weights and needs no BuildKit. Verified separately:

```
$ uvx gdown==6.4.2 --help          # ok: -O, --retries N (default 0), --continue, --json
```

`--retries 5` in the `RUN` is from that: gdown retries zero times by default while the store throttles
and terminates long transfers. `mkdir -p /weights` is there because `-O` takes a path and never
documents creating a parent directory. The assertion's two paths were exercised with `sh` on a dummy
file: it exits 0 on a match and 1 with the expected/actual message otherwise.

Not verified here: a full `docker build`. It pulls ~10 GB and the checkpoint is 3.5 GB, so the first
real build is on the machine that will run it.

## The archive that travels

```
$ git -C models archive --format=zip --prefix=sam31_official/ -o /tmp/opencode/prefixed.zip HEAD:sam31_official
$ unzip -l … | tail -2
  4976558                     242 files        # 2.19 MB zipped, no checkpoint inside
```

Two things this cost:

- **`--prefix` is not optional in practice.** Plain `git archive HEAD:sam31_official` strips the
  leading directory and extracts the Dockerfile and `vendor/` into whatever directory the target
  happens to be in. The prefix is what makes "unzip and build" a sentence with no conditions.
- **A relative `-o` with `git -C` writes into the wrong repository.** `git -C models archive -o
  prefixed.zip` put a 2 MB zip inside the submodule's working tree during this session (removed). Every
  documented form therefore names a directory explicitly, and that directory is `build/` — see below.

**Where the archives live, and why the directory exists.** `models/build/`, gitignored. That trap is
what produced it: a package written wherever the shell happens to be ends up in the wrong repository or
in someone's home, and a dedicated ignored directory is where "pack it" has exactly one answer — the
next step after packing is `scp`, and it should not need a decision.

    git archive --format=zip --prefix=sam31_official/ \
        -o build/sam31_official-$(git rev-parse --short HEAD).zip HEAD:sam31_official

It holds the **current** archive of each model folder, named `<folder>-<sha8>.zip`, and is regenerated
rather than accumulated: a bare name that gets overwritten leaves two hosts looking identical when
their code is not, which is why the commit id stays in the filename. The archive carries the store's
id, so it stays inside the private submodule — which is also the first place a deployment looks for it,
and the reason this is not a folder in someone's home.

`weights/` is now in `.dockerignore`, so the build context stays a few megabytes even on a machine that
does have a local checkpoint staged.

## What to watch

- **Replacing the store's file means updating the `ARG` and the size table together.** The `ARG` is what
  busts the layer cache: with a literal in the `RUN`, a swapped id would rebuild from the previous
  download still cached, and the failure would look like a model that answers wrongly rather than like
  a stale build.
- **No image built this way may be published.** It carries the link. That is the condition the rule
  change rests on.
- **The fetch belongs in every later container** — `sam2.1`, `cascade_psp`, `birefnet` — and step 3 of
  `sam31_official/README.md` now says so. None of them exists yet.
