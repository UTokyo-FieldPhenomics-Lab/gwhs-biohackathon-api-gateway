# Where the image bytes were, and what made them

2026-10-17. Two questions that turned out to be one: the 3090's root partition filled to 100 %, and the
image on it was 11.6 GB larger than it needed to be. The fixes are in
[`../../models/sam31_official/Dockerfile`](../../models/sam31_official/Dockerfile) and in that machine's
containerd configuration; what follows is how both were found, because neither was visible from the
number in front of us.

## The 98 GB partition, and a `data-root` that was not one

The trigger was an experiment that failed with `No space left on device` while unpacking torch — in a
throwaway container, on a machine whose `daemon.json` said its Docker storage was on a 393 GB partition
with 121 GB free. The failed container (`clever_volhard`, mine) then held **10.8 GB** of writable layer,
which is what took `/` from "full" to "full with nothing to spare"; removing it was the first fix.

The reason `data-root` did not mean what it said: **Docker here uses the containerd image store.**

    $ docker info | grep -A1 'Storage Driver'
     Storage Driver: overlayfs
      driver-type: io.containerd.snapshotter.v1

With that store, images and layers live in **containerd's** root — `/var/lib/containerd`, on the 98 GB
`/` — while `data-root` moves only Docker's own metadata and volumes. Three checks settled it: a
container's own `df` reported `/`'s filesystem, `/var/lib/docker` did not exist at all, and
`containerd` was running with the default root. So the line in `daemon.json` that was added to keep
images off the small partition had never moved anything. The honest measurement of "where Docker puts
its bytes" is `driver-type` plus the daemon's own `--root`, not `Docker Root Dir`.

**The move** (done in a parallel session, verified here): a systemd drop-in for containerd —
`ExecStart=/usr/bin/containerd --root /home/crest/d/DockerCache/container` — with Docker's `data-root`
beside it in the same tree. `/home/crest/d` is its own **1.4 TB** disk, so the store went from a 98 GB
partition at 100 % to a disk with 500+ GB free; `/` came back to **58 %**. Two things worked out better
than expected and are worth remembering: the images survived because the store was *moved*, not
recreated, and the containers came back on their own when the daemon restarted — `--restart
unless-stopped` doing exactly what the reboot section promises, on a restart that was not a reboot.

## The image: 21.9 GB of layers, 7.1 GB of it useful

`docker images` said 36.2 GB; the honest number is the layer sum from `docker history` — **21.9 GB**,
of which the virtualenv was 7.1 GB and the weights 3.3 GB. The largest single layer was
`uv sync --frozen`, at **14.9 GB**, while the finished filesystem had `/app/.venv` at 7.1 GB and
`/root` at **44 KB**.

Two causes, and both had to go:

- **The cache was committed, then deleted a layer too late.** uv writes its unpacked wheel cache into
  `/root/.cache/uv` while installing; the `rm -rf /root/.cache/uv` that removed it sat in the *next*
  RUN. A layer is a diff, so a later deletion adds a whiteout and leaves the bytes where they were:
  invisible in the container's filesystem, present in every `pull`, `save` and `push`. The 7.8 GB
  difference between that layer and the venv was exactly that cache.
- **`UV_LINK_MODE=copy` doubled it on the way.** uv's default on Linux is `hardlink`, and with a cache
  on the same filesystem the venv's files would be *names* for the cache's inodes. `copy` made them
  independent bytes instead — so this layer held every wheel twice.

**The fix, measured:** `UV_NO_CACHE=1` (uv unpacks into a temporary directory and leaves nothing, so
there is no cache to delete anywhere; the two `rm` calls are gone, including the one after `uvx gdown`)
and the link-mode override removed, so the temporary directory is hardlinked into `/app/.venv`. Same
machine, same legacy builder, same everything else:

| | before | after |
|---|---|---|
| `docker images` | 36.2 GB | **24.6 GB** |
| layer sum | 21.9 GB | **14.6 GB** |
| the `uv sync` layer | 14.9 GB | **7.54 GB** (≈ the 7.1 GB venv) |

The old image's layers were then removed, and the running container rebuilt onto the new tag rather
than kept on the old one: `/health` 200 locally and from the gateway host, `backends_up: 2`, and a
predict answering 200 in 1.5 s with the same object as before the rebuild.

## What was not measured, and what was left alone

- **The build's peak disk was not captured.** The sampler watched `/home` while the store had just
  moved to `/home/crest/d`, a different disk, so it recorded nothing. What bounds the peak now is
  structural rather than measured: no cache plus a hardlinked install means the transient is the venv
  being built plus one unpacked wheel.
- **The 5090's image was not rebuilt.** It still carries the pre-change build and has 920 GB free, so
  the saving there buys nothing today; a rebuild picks it up whenever one is needed for another reason.
- **The stray `serve.py` on `:8125`** (a `test-…` token, several GB of the card) and the **leftover
  container** were both removed in the parallel session, which also released the VRAM the stray held.

The lesson for the next machine: `docker images` reports its own accounting and `Docker Root Dir` may
name a directory that holds no layers. `docker history` for the layers, `driver-type` plus the daemon's
`--root` for where they physically are.
