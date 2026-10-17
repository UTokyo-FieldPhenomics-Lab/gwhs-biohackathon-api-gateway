# The 3090 becomes a container

2026-10-17, morning. The laboratory 3090 (`labpc-host`, `lab-3090`) served the pool from a bare
`uv run python serve.py` for the whole of 2026-10-16; it now runs `sam3.1-meta` as a container, on
`12711`, with `--restart unless-stopped`. That closes the shape question
[`2026101622`](2026101622_moving-the-backend-port-to-12711.md) left open, and with it the hole the
"Surviving a reboot" section assumes away: nothing supervised the bare process, so a reboot or the end
of its shell removed the pool's second backend in silence.

## Why it had not happened already

**No NVIDIA container toolkit.** The attempt of 2026-10-16 left its fingerprints — `nvidia/cuda:12.8.1-runtime-ubuntu24.04`
and `ghcr.io/astral-sh/uv:0.12` cached in the local daemon, no model image — and the answer to "why" was
one command away:

    $ docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi -L
    docker: Error response from daemon: failed to discover GPU vendor from CDI: no known GPU vendor found

`--gpus all` does not fall back to the host's driver; without the toolkit it fails, and the pool ended up
running the model on the host instead. Installing the toolkit needs `sudo`, which is why this waited for
a human: `sudo pacman -S --needed nvidia-container-toolkit`, `sudo nvidia-ctk runtime configure
--runtime=docker`, `sudo systemctl restart docker`. The per-distribution version of that, for the Arch
and Debian families, is now in [`deploy/gpu-backends.md`](../../deploy/gpu-backends.md).

**The one line worth checking afterwards:** `nvidia-ctk runtime configure` rewrites
`/etc/docker/daemon.json`, and on this machine that file carries `{"data-root": "/home/crest/docker"}` —
the only reason a 98 GB root partition is not the constraint it looks like. The merge kept it and added
the `nvidia` runtime; had it dropped the key, Docker would have started using `/var/lib/docker` and every
image would have looked lost while sitting untouched in the old directory.

## The build, on a machine without buildx

This daemon's `docker build` is the **legacy** builder (no `buildx` plugin, so no `--mount`, no
`--secret`, no `--check`), which is why the Dockerfile was written the way it was — a plain `RUN` with
`rm -rf /root/.cache/uv` rather than BuildKit's cache mount. It built all 16 steps in one pass, including
the `gdown` fetch and its byte assertion, and the image answers on the port the Dockerfile bakes in:

    docker run -d --gpus all -p 12711:12711 --restart unless-stopped \
      -e BACKEND_TOKEN=… --name sam3.1-meta sam3.1-meta

No `-e SAM31_PORT`: that the container came up on 12711 *is* the test of the default this repository
changed the previous evening. `budget ok: needs 5.5 GB, budget 23.0 GB (15281 MiB free of 24576 MiB,
1.0 GB held back)`, `ready: load 8.44 s, warm prompt 0.73 s`, `/health` 200 locally and **from the
gateway host**, and through the gateway `backends_up: 2` with a predict answering 200 in 1.55 s (one
object, area 173716). The host's `models.json` needed no edit: same address, same port.

## The image is 36 GB here and 13.9 GB there, and the layers say why

`docker images` reports 36.2 GB on this machine against the 5090's 13.9 GB, and `docker history` locates
the difference:

    14.9GB   uv sync --frozen --no-install-project
     3.5GB   WEIGHTS_BYTES=3502755717 WEIGHTS_ID=… (the fetch)
     ~5.6GB  the nvidia base, below that

The `uv sync` layer carries the venv *and* uv's own wheel cache, because nothing removes it in that step
(`UV_LINK_MODE=copy` and `UV_COMPILE_BYTECODE=1` add to it as well); the legacy builder keeps every
intermediate layer, and `docker images` counts them uncompressed. **Not fixed here** — it would mean
another rebuild — but the same one-line trick already used after `gdown` (`&& rm -rf /root/.cache/uv`)
would take several gigabytes off every image on both machines. Worth doing when the next model's
Dockerfile is written, or when a rebuild is needed anyway.

## Space, which turned out not to be the question

Docker's storage is `/home/crest/docker`, on a 393 GB partition with **121 GB free**; the root partition's
35 GB never mattered. So freeing the venv was never necessary, and the request was answered by measuring
rather than by deleting:

- **`weights/sam3.1_multiplex.pt`, 3.5 GB — removed**, as asked, once the image was up and serving. The
  container carries its own copy fetched during the build, and the host file was only ever the smoke
  test's; one `gdown` command restores it, and `weights/README.md` stays so the directory exists.
- **`.venv`, 7.0 GB — kept.** It is what `uv run pytest` and `infer.py` use in this checkout, and the
  checkout is where the containers are developed. Deleting it would cost a re-download of several
  gigabytes, because uv's cache here is only 102 MB.
- Both repositories stayed clean through the deletion: the weights are ignored *by directory*, which is
  what that rule was written for.

## Outstanding

- **A stray `serve.py` on `:8125`**, six hours old, holding several gigabytes of the card, started with a
  `test-…` token that no list points at. Still not mine to kill.
- **5.7 GB of reclaimable images** on this machine (`dockur/windows`, the WinBoat VM, exited four months
  ago). `docker system prune -a` takes it; also someone's call.
- **The inventory row was corrected**, not just extended: it claimed `sshd` was "enabled and active" and
  that "GPU inside a container" had been verified on 2026-10-16. Port 22 **refuses** connections (from the
  gateway host and on the machine's own address), and the container claim was the failure described above.
